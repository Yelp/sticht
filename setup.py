# Copyright 2019 Yelp Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from setuptools import find_packages
from setuptools import setup


setup(
    name='sticht',
    version='1.1.18',
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
    ],
    install_requires=[
        'pytimeparse',
        'slackclient>=1.2.1',
        'transitions',
        'mypy_extensions',
        'signalfx',
        'splunk-sdk',
        'typing-extensions',
    ],
    extras_require={
        'yelp_internal': [
            'yelp-clog>=2.12.1',
            'slo-transcoder>=3.2.3',
        ],
    },
    packages=find_packages(exclude=('tests*', 'testing*')),
    package_data={
        'sticht': ['py.typed'],
    },
)
