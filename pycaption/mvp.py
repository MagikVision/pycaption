import json

from .webvtt import WebVTTReader


class InvalidWebVTT(Exception):
    pass


class MVPParser(WebVTTReader):

    def read(self, content):
        self.pre_validate(content)
        captions_list = super(MVPParser, self).read(content)
        return self.post_validate(captions_list=captions_list)

    def pre_validate(self, content):
        lines = content.splitlines()
        # webvtt should always start with `WEBVTT` and an empty line/s
        if 'WEBVTT' not in lines[0]:
            raise InvalidWebVTT('Header WebVTT is missing')
        # do not allow consecutive new lines
        for i, line in enumerate(lines):
            previous_line = self._get_previous_line(lines=lines, line_num=i)
            if line == previous_line and line == '':
                raise InvalidWebVTT('Consecutive new lines found')

    def post_validate(self, captions_list):
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
