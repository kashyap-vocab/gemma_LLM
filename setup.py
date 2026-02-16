from setuptools import setup
from Cython.Build import cythonize
from pathlib import Path

py_files = [
    str(p) for p in Path(".").rglob("*.py")
    if "venv" not in str(p)
    and "__pycache__" not in str(p)
]

setup(
    ext_modules=cythonize(
        py_files,
        compiler_directives={"language_level": "3"},
    ),
    zip_safe=False,
)
