import json
import datetime
import textwrap

from .webvtt import WebVTTReader


class InvalidWebVTT(Exception):
    pass


class MVPParser(WebVTTReader):

    def read(self, content):
        self._pre_validate(content)
        captions_list = super(MVPParser, self).read(content)
        return self._post_validate(captions_list=captions_list)

    def get_formatted(self, captions_list):
        cue_template = textwrap.dedent("""\
            {timespan}

            NOTE
            {metadata}

        """)
        formatted_cues = []
        for cue in captions_list.get_captions():
            start = self._timestamp(cue.start)
            end = self._timestamp(cue.end)
            timespan = "{} --> {}".format(start, end)
            metadata = str.strip(cue.get_comment()).strip('\n')
            cue_packet = cue_template.format(
                timespan=timespan, metadata=metadata)
            formatted_cues.append(cue_packet)
        return 'WEBVTT\n\n' + "".join(formatted_cues)

    def _pre_validate(self, content):
        lines = content.splitlines()
        # webvtt should always start with `WEBVTT` and an empty line/s
        if 'WEBVTT' not in lines[0]:
            raise InvalidWebVTT('Header WebVTT is missing')
        # do not allow consecutive new lines
        for i, line in enumerate(lines):
            previous_line = self._get_previous_line(lines=lines, line_num=i)
            if line == previous_line and line == '':
                raise InvalidWebVTT('Consecutive new lines found')

    def _post_validate(self, captions_list):
        for cue in captions_list.get_captions():
            comment = cue.get_comment()
            if not comment:
                raise InvalidWebVTT('Metadata not found')
            try:
                metadata = json.loads(comment)
            except json.JSONDecodeError:
                print('comment: %s' % comment)
                raise InvalidWebVTT('Metadata is not a valid JSON')
            if 'Seq' not in metadata:
                raise InvalidWebVTT('Metadata is missing Seq number')
            if 'game_id' not in metadata:
                raise InvalidWebVTT('Metadata is missing Game ID')
        return captions_list

    def _timestamp(self, ts):
        td = datetime.timedelta(microseconds=ts)
        mm, ss = divmod(td.seconds, 60)
        hh, mm = divmod(mm, 60)
        s = "%02d:%02d.%03d" % (mm, ss, td.microseconds/1000)
        if hh:
            s = "%d:%s" % (hh, s)
        return s
