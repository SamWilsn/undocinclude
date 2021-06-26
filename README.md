undocinclude
============

Sphinx plugin that provides a directive `undocinclude`, which is basically [`literalinclude`][0] but hacked to omit docstrings.

Supports a subset of `literalinclude`'s options.

## Installation

Add this package to your requirements, and modify your Sphinx `conf.py`:

```python
extensions = [
    # ...
    'undocinclude.extension',
    # ...
]
```

## Example

```rst
.. undocinclude:: /path/to/script.py
```

[0]: https://www.sphinx-doc.org/en/master/usage/restructuredtext/directives.html#directive-literalinclude
