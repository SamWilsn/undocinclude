import ast

from typing import Dict, List, Tuple, Set

from docutils.parsers.rst import directives
from docutils.nodes import Element, Node
from docutils import nodes

from sphinx.util import logging, parselinenos
from sphinx.directives import SphinxDirective
from sphinx.directives.code import container_wrapper
from sphinx.config import Config
from sphinx.locale import __
from sphinx.util.typing import OptionSpec


logger = logging.getLogger(__name__)


class UndocIncludeReader:
    INVALID_OPTIONS_PAIR = [
        ('lineno-match', 'lineno-start'),
    ]

    def __init__(self, filename: str, options: Dict, config: Config) -> None:
        self.filename = filename
        self.options = options
        self.encoding = options.get('encoding', config.source_encoding)
        self.lineno_start = self.options.get('lineno-start', 1)

        self.parse_options()

    def parse_options(self) -> None:
        for option1, option2 in self.INVALID_OPTIONS_PAIR:
            if option1 in self.options and option2 in self.options:
                raise ValueError(__('Cannot use both "%s" and "%s" options') %
                                 (option1, option2))

    def read_file(self, filename: str, location: Tuple[str, int] = None) -> List[str]:
        try:
            with open(filename, encoding=self.encoding, errors='strict') as f:
                text = f.read()
                if 'tab-width' in self.options:
                    text = text.expandtabs(self.options['tab-width'])

                return text.splitlines(True)
        except OSError as exc:
            raise OSError(__('Include file %r not found or reading it failed') %
                          filename) from exc
        except UnicodeError as exc:
            raise UnicodeError(__('Encoding %r used for reading included file %r seems to '
                                  'be wrong, try giving an :encoding: option') %
                               (self.encoding, filename)) from exc

    def read(self, location: Tuple[str, int] = None) -> Tuple[str, int]:
        filters = [self.pyobject_filter, self.lines_filter]
        lines = self.read_file(self.filename, location=location)
        parsed = ast.parse(''.join(lines), filename=self.filename)
        parsed = ast.fix_missing_locations(parsed)

        # Yes, using a set for this is inefficient. I give you permission to
        # make fun of me when this becomes a problem.
        self.docstring_lines: Set[int] = set()
        for node in ast.walk(parsed):
            docstring = None
            try:
                docstring = ast.get_docstring(node)
            except TypeError:
                pass

            if docstring is None:
                continue

            body = node.body  # type: ignore
            docstring_node = body[0].value

            try:
                # Python >= 3.8
                end_lineno = docstring_node.end_lineno
                lineno = docstring_node.lineno - 1
            except AttributeError:
                # Python 3.7
                end_lineno = docstring_node.lineno
                lineno = end_lineno - len(docstring_node.s.split('\n'))

            self.docstring_lines |= set(range(
                lineno,
                end_lineno
            ))

        filter_lines = [(line, True) for line in lines]

        for func in filters:
            filter_lines = func(filter_lines, location=location)

        lines = [line[0] for line in filter_lines if line[1]]

        return ''.join(lines), len(lines)

    def pyobject_filter(self, lines: List[Tuple[str, bool]], location: Tuple[str, int] = None) -> List[Tuple[str, bool]]:
        pyobject = self.options.get('pyobject')
        if pyobject:
            from sphinx.pycode import ModuleAnalyzer
            analyzer = ModuleAnalyzer.for_file(self.filename, '')
            tags = analyzer.find_tags()
            if pyobject not in tags:
                raise ValueError(__('Object named %r not found in include file %r') %
                                 (pyobject, self.filename))
            else:
                start = tags[pyobject][1]
                end = tags[pyobject][2]
                r = range(start - 1, end)
                lines = [(t, i and n in r) for (n, (t, i)) in enumerate(lines)]
                if 'lineno-match' in self.options:
                    self.lineno_start = start

        return lines

    def lines_filter(self, lines: List[Tuple[str, bool]], location: Tuple[str, int] = None) -> List[Tuple[str, bool]]:
        linespec = self.options.get('lines')
        if linespec:
            linelist = parselinenos(linespec, len(lines))
        else:
            linelist = list(range(len(lines)))

        print(f"location={location} linespec={linespec} linelist={linelist} options={self.options}")

        if self.docstring_lines:
            linelist = list(set(linelist) - self.docstring_lines)
            linelist.sort()

        if any(i >= len(lines) for i in linelist):
            logger.warning(__('line number spec is out of range(1-%d): %r') %
                           (len(lines), linespec), location=location)

        if 'lineno-match' in self.options:
            # make sure the line list is not "disjoint".
            first = linelist[0]
            if all(first + i == n for i, n in enumerate(linelist)):
                self.lineno_start += linelist[0]
            else:
                raise ValueError(__('Cannot use "lineno-match" with a disjoint '
                                    'set of "lines"'))

        lines = [(t, i and n in linelist) for (n, (t, i)) in enumerate(lines)]
        if not any(v for (_, v) in lines):
            raise ValueError(__('Line spec %r: no lines pulled from include file %r') %
                             (linespec, self.filename))

        return lines


class UndocInclude(SphinxDirective):
    """
    Like ``.. include:: :literal:``, but only warns if the include file is
    not found, and does not raise errors.  Also has several options for
    selecting what to include.
    """

    has_content = False
    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = True
    option_spec: OptionSpec = {
        'linenos': directives.flag,
        'lineno-start': int,
        'lineno-match': directives.flag,
        'tab-width': int,
        'language': directives.unchanged_required,
        'force': directives.flag,
        'encoding': directives.encoding,
        'pyobject': directives.unchanged_required,
        'lines': directives.unchanged_required,
        'prepend': directives.unchanged_required,
        'append': directives.unchanged_required,
        'emphasize-lines': directives.unchanged_required,
        'caption': directives.unchanged,
        'class': directives.class_option,
        'name': directives.unchanged,
    }

    def run(self) -> List[Node]:
        document = self.state.document
        if not document.settings.file_insertion_enabled:
            return [document.reporter.warning('File insertion disabled',
                                              line=self.lineno)]

        try:
            location = self.state_machine.get_source_and_line(self.lineno)
            rel_filename, filename = self.env.relfn2path(self.arguments[0])
            self.env.note_dependency(rel_filename)

            reader = UndocIncludeReader(filename, self.options, self.config)
            text, lines = reader.read(location=location)

            retnode: Element = nodes.literal_block(text, text, source=filename)
            retnode['force'] = 'force' in self.options
            self.set_source_info(retnode)
            if 'language' in self.options:
                retnode['language'] = self.options['language']
            if ('linenos' in self.options or 'lineno-start' in self.options or
                    'lineno-match' in self.options):
                retnode['linenos'] = True
            retnode['classes'] += self.options.get('class', [])
            extra_args = retnode['highlight_args'] = {}
            if 'emphasize-lines' in self.options:
                hl_lines = parselinenos(self.options['emphasize-lines'], lines)
                if any(i >= lines for i in hl_lines):
                    logger.warning(__('line number spec is out of range(1-%d): %r') %
                                   (lines, self.options['emphasize-lines']),
                                   location=location)
                extra_args['hl_lines'] = [x + 1 for x in hl_lines if x < lines]
            extra_args['linenostart'] = reader.lineno_start

            if 'caption' in self.options:
                caption = self.options['caption'] or self.arguments[0]
                retnode = container_wrapper(self, retnode, caption)

            # retnode will be note_implicit_target that is linked from caption and numref.
            # when options['name'] is provided, it should be primary ID.
            self.add_name(retnode)

            return [retnode]
        except Exception as exc:
            return [document.reporter.warning(exc, line=self.lineno)]
