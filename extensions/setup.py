from setuptools import setup, Extension
from Cython.Build import cythonize

extensions = [
    Extension(
        name="prompt_builder",
        sources=["prompt_builder.pyx"],
        extra_compile_args=["-O3", "-march=native"],
    ),
]

setup(
    name="vllm_extensions",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": 3,
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
        },
    ),
)
