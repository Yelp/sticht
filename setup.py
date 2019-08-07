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
        'slackclient >= 1.2.1',
        'transitions',
        'mypy_extensions',
        'signalfx',
    ],
    extras_require={
        'yelp_internal': [
            'yelp-clog>=2.12.1',
            'slo-transcoder>=2.5.6',
        ],
    },
    packages=find_packages(exclude=('tests*', 'testing*')),
)
