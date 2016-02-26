import datetime
import sys
import re
from copy import deepcopy

from builtins import str
import six

from .base import (
    BaseReader, BaseWriter, CaptionSet, CaptionList, Caption, CaptionNode
)

from .geometry import Layout

from .exceptions import (
    CaptionReadError, CaptionReadSyntaxError, CaptionReadNoCaptions,
    InvalidInputError
)

# A WebVTT timing line has both start/end times and layout related settings
# (referred to as 'cue settings' in the documentation)
# The following pattern captures [start], [end] and [cue settings] if existent
from pycaption.geometry import HorizontalAlignmentEnum

TIMING_LINE_PATTERN = re.compile(u'^(\S+)\s+-->\s+(\S+)(?:\s+(.*?))?\s*$')
TIMESTAMP_PATTERN = re.compile(u'^(\d+):(\d{2})(:\d{2})?\.(\d{3})')
VOICE_SPAN_PATTERN = re.compile(u'<v(\\.\\w+)* ([^>]*)>')
OTHER_SPAN_PATTERN = (
    re.compile(
        u'</?([cibuv]|ruby|rt|lang|(\d+):(\d{2})(:\d{2})?\.(\d{3})).*?>'
    )
)  # These WebVTT tags are stripped off the cues on conversion

WEBVTT_VERSION_OF = {
    HorizontalAlignmentEnum.LEFT: u'left',
    HorizontalAlignmentEnum.CENTER: u'middle',
    HorizontalAlignmentEnum.RIGHT: u'right',
    HorizontalAlignmentEnum.START: u'start',
    HorizontalAlignmentEnum.END: u'end'
}

DEFAULT_ALIGNMENT = u'middle'


def microseconds(h, m, s, f):
    """
    Returns an integer representing a number of microseconds
    :rtype: int
    """
    return (int(h) * 3600 + int(m) * 60 + int(s)) * 1000000 + int(f) * 1000


class WebVTTReader(BaseReader):
    def __init__(self, ignore_timing_errors=True, *args, **kwargs):
        """
        :param ignore_timing_errors: Whether to ignore timing checks
        """
        self.ignore_timing_errors = ignore_timing_errors

    def detect(self, content):
        return u'WEBVTT' in content

    def read(self, content, lang=u'en-US'):
        if type(content) != six.text_type:
            raise InvalidInputError('The content is not a unicode string.')

        caption_set = CaptionSet({lang: self._parse(content.splitlines())})

        if caption_set.is_empty():
            raise CaptionReadNoCaptions(u"empty caption file")

        return caption_set

    def _parse(self, lines):
        captions = CaptionList()
        start = None
        end = None
        nodes = []
        layout_info = None
        found_timing = False
        found_note = False

        for i, line in enumerate(lines):

            previous_line = self._get_previous_line(lines=lines, line_num=i)
            next_line = self._get_next_line(lines=lines, line_num=i)

            if u'-->' in line:
                found_timing = True
                timing_line = i
                last_start_time = captions[-1].start if captions else 0
                try:
                    start, end, layout_info = self._parse_timing_line(
                        line, last_start_time)
                except CaptionReadError as e:
                    new_message = u'%s (line %d)' % (e.args[0], timing_line)
                    six.reraise(
                        type(e), type(e)(new_message), sys.exc_info()[2])

            elif line == 'NOTE' and found_note and previous_line == '':
                continue

            elif u'' == line:
                if next_line == 'NOTE' and found_timing:
                    found_note = True
                elif found_timing:
                    if not nodes:
                        # print('Cue without subtitle')
                        pass
                    else:
                        found_timing = False
                        found_note = False
                        caption = Caption(
                            start, end, nodes, layout_info=layout_info)
                        captions.append(caption)
                        nodes = []
            else:
                if found_note:
                    nodes.append(CaptionNode.create_comment(line))
                elif found_timing:
                    if nodes:
                        nodes.append(CaptionNode.create_break())
                    nodes.append(CaptionNode.create_text(
                        self._decode(line)))
                else:
                    # it's a comment or some metadata; ignore it
                    # print('ignored %s' % line)
                    pass

        # Add a last caption if there are remaining nodes
        if nodes:
            caption = Caption(start, end, nodes, '', layout_info=layout_info)
            captions.append(caption)
        return captions

    def _remove_styles(self, line):
        partial_result = VOICE_SPAN_PATTERN.sub(u'\\2: ', line)
        return OTHER_SPAN_PATTERN.sub(u'', partial_result)

    def _validate_timings(self, start, end, last_start_time):
        if start is None:
            raise CaptionReadSyntaxError(
                u'Invalid cue start timestamp.')
        if end is None:
            raise CaptionReadSyntaxError(u'Invalid cue end timestamp.')
        if start > end:
            raise CaptionReadError(
                u'End timestamp is not greater than start timestamp.')
        if start < last_start_time:
            raise CaptionReadError(
                u'Start timestamp is not greater than or equal'
                u'to start timestamp of previous cue.')

    def _parse_timing_line(self, line, last_start_time):
        """
        :returns: Tuple (int, int, Layout)
        """
        m = TIMING_LINE_PATTERN.search(line)
        if not m:
            raise CaptionReadSyntaxError(
                u'Invalid timing format.')

        start = self._parse_timestamp(m.group(1))
        end = self._parse_timestamp(m.group(2))

        cue_settings = m.group(3)

        if not self.ignore_timing_errors:
            self._validate_timings(start, end, last_start_time)

        layout_info = None
        if cue_settings:
            layout_info = Layout(webvtt_positioning=cue_settings)

        return start, end, layout_info

    def _parse_timestamp(self, timestamp):
        """Returns an integer representing a number of microseconds
        :rtype: int
        """
        m = TIMESTAMP_PATTERN.search(timestamp)
        if not m:
            raise CaptionReadSyntaxError(
                u'Invalid timing format.')

        m = m.groups()

        if m[2]:
            # Timestamp takes the form of
            # [hours]:[minutes]:[seconds].[milliseconds]
            return microseconds(m[0], m[1], m[2].replace(u":", u""), m[3])
        else:
            # Timestamp takes the form of [minutes]:[seconds].[milliseconds]
            return microseconds(0, m[0], m[1], m[3])

    def _decode(self, s):
        """
        Convert cue text from WebVTT XML-like format to plain unicode.
        :type s: unicode
        """
        s = s.strip()
        # Covert voice span
        s = VOICE_SPAN_PATTERN.sub(u'\\2: ', s)
        # TODO: Add support for other WebVTT tags. For now just strip them
        # off the text.
        s = OTHER_SPAN_PATTERN.sub(u'', s)
        # Replace WebVTT special XML codes with plain unicode values
        s = s.replace(u'&lt;', u'<')
        s = s.replace(u'&gt;', u'>')
        s = s.replace(u'&lrm;', u'\u200e')
        s = s.replace(u'&rlm;', u'\u200f')
        s = s.replace(u'&nbsp;', u'\u00a0')
        # Must do ampersand last
        s = s.replace(u'&amp;', u'&')
        return s

    def _get_previous_line(self, lines, line_num):
        if line_num < 1:
            previous_line = None
        else:
            previous_line = lines[line_num-1]
        return previous_line

    def _get_next_line(self, lines, line_num):
        try:
            return lines[line_num + 1]
        except IndexError:
            return None


class WebVTTWriter(BaseWriter):
    HEADER = u'WEBVTT\n\n'
    global_layout = None
    video_width = None
    video_height = None

    def write(self, caption_set):
        """
        :type caption_set: CaptionSet
        """
        output = self.HEADER

        if caption_set.is_empty():
            return output

        caption_set = deepcopy(caption_set)

        # TODO: styles. These go into a separate CSS file, which doesn't really
        # fit the API here. Figure that out.  Though some style stuff can be
        # done in-line.  This format is a little bit crazy.

        # WebVTT's language support seems to be a bit crazy, so let's just
        # support a single one for now.
        lang = list(caption_set.get_languages())[0]

        self.global_layout = caption_set.get_layout_info(lang)

        captions = caption_set.get_captions(lang)

        return output + u'\n'.join(
            [self._write_caption(caption_set, caption) for caption in captions])

    def _timestamp(self, ts):
        td = datetime.timedelta(microseconds=ts)
        mm, ss = divmod(td.seconds, 60)
        hh, mm = divmod(mm, 60)
        s = "%02d:%02d.%03d" % (mm, ss, td.microseconds/1000)
        if hh:
            s = "%d:%s" % (hh,s)
        return s


    def _tags_for_style(self, style):
        if style == u'italics':
            return [u'<i>', u'</i>']
        elif style == u'underline':
            return [u'<u>', u'</u>']
        elif style == u'bold':
            return [u'<b>', u'</b>']
        else:
            return [u'', u'']

    def _calculate_resulting_style(self, style, caption_set):
        resulting_style = {}

        style_classes = []
        if u'classes' in style:
            style_classes = style[u'classes']
        elif u'class' in style:
            style_classes = [style[u'class']]

        for style_class in style_classes:
            sub_style = caption_set.get_style(style_class).copy()
            # Recursively resolve class attributes and calculate style
            resulting_style.update(self._calculate_resulting_style(sub_style, caption_set))

        resulting_style.update(style)

        return resulting_style

    def _write_caption(self, caption_set, caption):
        """
        :type caption: Caption
        """
        layout_groups = self._layout_groups(caption.nodes, caption_set)

        start = self._timestamp(caption.start)
        end = self._timestamp(caption.end)
        timespan = u"{} --> {}".format(start, end)

        output = u''

        cue_style_tags = [u'', u'']

        style = self._calculate_resulting_style(caption.style, caption_set)
        for key, value in sorted(style.items()):
            if value:
                tags = self._tags_for_style(key)
#                    print "tags: " + str(tags) + "\n"
                cue_style_tags[0] += tags[0]
                cue_style_tags[1]  = tags[1] + cue_style_tags[1]

        for cue_text, layout in layout_groups:
            if not layout:
                layout = caption.layout_info or self.global_layout
            cue_settings = self._cue_settings_from(layout)
            output += timespan + cue_settings + u'\n'
            output += cue_style_tags[0] + cue_text + cue_style_tags[1] + u'\n'

        return output

    def _cue_settings_from(self, layout):
        """
        Return WebVTT cue settings string based on layout info
        :type layout: Layout
        :rtype: unicode
        """
        if not layout:
            return u''

        # If it's converting from WebVTT to WebVTT, keep positioning info
        # unchanged
        if layout.webvtt_positioning:
            return u' {}'.format(layout.webvtt_positioning)

        left_offset = None
        top_offset = None
        cue_width = None
        alignment = None

        already_relative = False
        if not self.relativize:
            if layout.is_relative():
                already_relative = True
            else:
                # There are absolute positioning values for this cue but the
                # Writer is explicitly configured not to do any relativization.
                # Ignore all positioning for this cue.
                return u''

        # Ensure that all positioning values are measured using percentage.
        # This may raise an exception if layout.is_relative() == False
        # If you want to avoid it, you have to turn off relativization by
        # initializing this Writer with relativize=False.
        if not already_relative:
            layout = layout.as_percentage_of(
                self.video_width, self.video_height)

        # Ensure that when there's a left offset the caption is not pushed out
        # of the screen. If the execution got this far it means origin and
        # extent are already relative by now.
        if self.fit_to_screen:
            layout = layout.fit_to_screen()

        if layout.origin:
            left_offset = layout.origin.x
            top_offset = layout.origin.y

        if layout.extent:
            cue_width = layout.extent.horizontal

        if layout.padding:
            if layout.padding.start and left_offset:
                # Since there is no padding in WebVTT, the left padding is
                # added to the total left offset (if it is defined and not
                # relative),
                if left_offset:
                    left_offset += layout.padding.start
                # and removed from the total cue width
                if cue_width:
                    cue_width -= layout.padding.start
            # the right padding is cut out of the total cue width,
            if layout.padding.end and cue_width:
                cue_width -= layout.padding.end
            # the top padding is added to the top offset
            # (if it is defined and not relative)
            if layout.padding.before and top_offset:
                top_offset += layout.padding.before
            # and the bottom padding is ignored because the cue box is only as
            # long vertically as the text it contains and nothing can be cut
            # out

        try:
            alignment = WEBVTT_VERSION_OF[layout.alignment.horizontal]
        except (AttributeError, KeyError):
            pass

        cue_settings = u''

        if alignment and alignment != u'middle':
            cue_settings += u" align:" + alignment
        if left_offset:
            cue_settings += u" position:{},start".format(six.text_type(left_offset))
        if top_offset:
            cue_settings += u" line:" + six.text_type(top_offset)
        if cue_width:
            cue_settings += u" size:" + six.text_type(cue_width)

        return cue_settings

    def _layout_groups(self, nodes, caption_set):
        """
        Convert a Caption's nodes to WebVTT cue or cues (depending on
        whether they have the same positioning or not).
        """
        if not nodes:
            return []

        current_layout = None

        # A list with layout groups. Since WebVTT only support positioning
        # for different cues, each layout group has to be represented in a
        # new cue with the same timing but different positioning settings.
        layout_groups = []
        # A properly encoded WebVTT string (plain unicode must be properly
        # escaped before being appended to this string)
        s = u''
        for i, node in enumerate(nodes):
            if node.type_ == CaptionNode.TEXT:
                if s and current_layout and node.layout_info != current_layout:
                    # If the positioning changes from one text node to
                    # another, a new WebVTT cue has to be created.
                    layout_groups.append((s, current_layout))
                    s = u''
                # ATTENTION: This is where the plain unicode node content is
                # finally encoded as WebVTT.
                s += self._encode(node.content) or u'&nbsp;'
                current_layout = node.layout_info
            elif node.type_ == CaptionNode.STYLE:
                resulting_style = self._calculate_resulting_style(node.content, caption_set)

                styles = [u'italics', u'underline', u'bold']
                if not node.start:
                    styles.reverse()

                for style in styles:
                    if style in resulting_style and resulting_style[style]:
                        tags = self._tags_for_style(style)
                        if node.start:
                            s += tags[0]
                        else:
                            s += tags[1]

                # TODO: Refactor pycaption and eliminate the concept of a
                # "Style node"
            elif node.type_ == CaptionNode.BREAK:
                if i > 0 and nodes[i - 1].type_ != CaptionNode.TEXT:
                    s += u'&nbsp;'
                if i == 0:  # cue text starts with a break
                    s += u'&nbsp;'
                s += u'\n'

        if s:
            layout_groups.append((s, current_layout))
        return layout_groups

    def _encode(self, s):
        """
        Convert cue text from plain unicode to WebVTT XML-like format
        escaping illegal characters. For a list of illegal characters see:
            - http://dev.w3.org/html5/webvtt/#dfn-webvtt-cue-text-span
        :type s: unicode
        """
        s = s.replace(u'&', u'&amp;')
        s = s.replace(u'<', u'&lt;')

        # The substring "-->" is also not allowed according to this:
        #   - http://dev.w3.org/html5/webvtt/#dfn-webvtt-cue-block
        s = s.replace(u'-->', u'--&gt;')

        # The following characters have escaping codes for some reason, but
        # they're not illegal, so for now I'll leave this commented out so that
        # we stay as close as possible to the specification and avoid doing
        # extra stuff "just to be safe".
        # s = s.replace(u'>', u'&gt;')
        # s = s.replace(u'\u200e', u'&lrm;')
        # s = s.replace(u'\u200f', u'&rlm;')
        # s = s.replace(u'\u00a0', u'&nbsp;')
        return s
