"""
Setup file for the fitdays package
"""

from setuptools import setup

setup(
    name="fitdays",
    version="1.0.0",
    description=(
        "An unofficial async Python client for the Fitdays (ICOMON) smart-scale "
        "cloud API"
    ),
    author="AboveColin",
    author_email="colin@cdevries.dev",
    packages=["fitdays"],
    install_requires=[
        "aiohttp",
    ],
    python_requires=">=3.11",
    url="https://github.com/abovecolin/fitdays",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
    ],
    long_description_content_type="text/markdown",
    long_description=open("README.md", encoding="utf-8").read(),
)
