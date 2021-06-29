import ast

from typing import Dict, List, Tuple, Set

from docutils.parsers.rst import directives
from docutils.nodes import Element, Node
from docutils import nodes

from sphinx.util import logging, parselinenos
from sphinx.directives import SphinxDirective, optional_int
from sphinx.directives.code import dedent_lines, container_wrapper
from sphinx.config import Config
from sphinx.locale import __
from sphinx.util.typing import OptionSpec


logger = logging.getLogger(__name__)


class UndocIncludeReader:
    INVALID_OPTIONS_PAIR = [
        ('lineno-match', 'lineno-start'),
        ('lineno-match', 'append'),
        ('lineno-match', 'prepend'),
        ('start-after', 'start-at'),
        ('end-before', 'end-at'),
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
        filters = [self.pyobject_filter,
                   self.start_filter,
                   self.end_filter,
                   self.lines_filter,
                   self.prepend_filter,
                   self.append_filter,
                   self.dedent_filter]
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

        for func in filters:
            lines = func(lines, location=location)

        return ''.join(lines), len(lines)

    def pyobject_filter(self, lines: List[str], location: Tuple[str, int] = None) -> List[str]:
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
                lines = lines[start - 1:end]
                if 'lineno-match' in self.options:
                    self.lineno_start = start

        return lines

    def lines_filter(self, lines: List[str], location: Tuple[str, int] = None) -> List[str]:
        linespec = self.options.get('lines')
        if linespec:
            linelist = parselinenos(linespec, len(lines))
        else:
            linelist = range(len(lines))

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

        lines = [lines[n] for n in linelist if n < len(lines)]
        if lines == []:
            raise ValueError(__('Line spec %r: no lines pulled from include file %r') %
                             (linespec, self.filename))

        return lines

    def start_filter(self, lines: List[str], location: Tuple[str, int] = None) -> List[str]:
        if 'start-at' in self.options:
            start = self.options.get('start-at')
            inclusive = False
        elif 'start-after' in self.options:
            start = self.options.get('start-after')
            inclusive = True
        else:
            start = None

        if start:
            for lineno, line in enumerate(lines):
                if start in line:
                    if inclusive:
                        if 'lineno-match' in self.options:
                            self.lineno_start += lineno + 1

                        return lines[lineno + 1:]
                    else:
                        if 'lineno-match' in self.options:
                            self.lineno_start += lineno

                        return lines[lineno:]

            if inclusive is True:
                raise ValueError('start-after pattern not found: %s' % start)
            else:
                raise ValueError('start-at pattern not found: %s' % start)

        return lines

    def end_filter(self, lines: List[str], location: Tuple[str, int] = None) -> List[str]:
        if 'end-at' in self.options:
            end = self.options.get('end-at')
            inclusive = True
        elif 'end-before' in self.options:
            end = self.options.get('end-before')
            inclusive = False
        else:
            end = None

        if end:
            for lineno, line in enumerate(lines):
                if end in line:
                    if inclusive:
                        return lines[:lineno + 1]
                    else:
                        if lineno == 0:
                            pass  # end-before ignores first line
                        else:
                            return lines[:lineno]
            if inclusive is True:
                raise ValueError('end-at pattern not found: %s' % end)
            else:
                raise ValueError('end-before pattern not found: %s' % end)

        return lines

    def prepend_filter(self, lines: List[str], location: Tuple[str, int] = None) -> List[str]:
        prepend = self.options.get('prepend')
        if prepend:
            lines.insert(0, prepend + '\n')

        return lines

    def append_filter(self, lines: List[str], location: Tuple[str, int] = None) -> List[str]:
        append = self.options.get('append')
        if append:
            lines.append(append + '\n')

        return lines

    def dedent_filter(self, lines: List[str], location: Tuple[str, int] = None) -> List[str]:
        if 'dedent' in self.options:
            dedent = self.options.get('dedent')
            if not isinstance(dedent, (int, str)):
                raise TypeError('dedent must be a str or int')
            return dedent_lines(lines, int(dedent), location=location)
        else:
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
        'dedent': optional_int,
        'linenos': directives.flag,
        'lineno-start': int,
        'lineno-match': directives.flag,
        'tab-width': int,
        'language': directives.unchanged_required,
        'force': directives.flag,
        'encoding': directives.encoding,
        'pyobject': directives.unchanged_required,
        'lines': directives.unchanged_required,
        'start-after': directives.unchanged_required,
        'end-before': directives.unchanged_required,
        'start-at': directives.unchanged_required,
        'end-at': directives.unchanged_required,
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
