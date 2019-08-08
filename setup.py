from setuptools import find_packages
from setuptools import setup


setup(
    name='sticht',
    version='1.0.0',
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
    ],
    install_requires=[
        'pytimeparse',
        'slackclient',
        'transitions',
        'mypy_extensions',
        'signalfx',
    ],
    packages=find_packages(exclude=('tests*', 'testing*')),
)
