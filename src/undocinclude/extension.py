from typing import Dict, Any
from sphinx.application import Sphinx
from .directives import UndocInclude


def setup(app: Sphinx) -> Dict[str, Any]:
    app.add_directive("undocinclude", UndocInclude)
    return {
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
