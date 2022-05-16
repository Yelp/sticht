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

.PHONY: minimal
minimal: venv

venv: requirements-dev.txt setup.py tox.ini
ifeq ($(findstring .yelpcorp.com,$(shell hostname -f)), .yelpcorp.com)
	tox -e venv-internal
else
	tox -e venv
endif

.PHONY: test
test:
ifeq ($(findstring .yelpcorp.com,$(shell hostname -f)), .yelpcorp.com)
	tox -e internal
else
	tox
endif

.PHONY: clean
clean:
	find -name '*.pyc' -delete
	find -name '__pycache__' -delete
	rm -rf .tox
	rm -rf venv
