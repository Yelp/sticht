# Sticht: simple automatic rollbacks

[![Build Status](https://travis-ci.org/Yelp/sticht.svg?branch=master)](https://travis-ci.org/Yelp/sticht)
[![Coverage Status](https://coveralls.io/repos/github/Yelp/sticht/badge.svg)](https://coveralls.io/github/Yelp/sticht)

Sticht is a library for building deployment workflows.
Workflows are encoded as state machines, using the [transitions](https://github.com/pytransitions/transitions) library.
Sticht lets users control the workflow via Slack, and makes it simple to write reusable code that triggers rollbacks when conditions are met, such as metrics exceeding thresholds.

We use this library within [Paasta](https://github.com/Yelp/paasta) for automatically rolling back deployments of code when SLOs start failing.

# Usage

To write a deployment workflow using Sticht, subclass one of:
- `sticht.state_machine.DeploymentProcess`
- `sticht.slack.SlackDeploymentProcess` (to get Slack buttons)
- `sticht.slo.SLOSlackDeploymentProcess` (to get both Slack buttons and automatic rollbacks when SLOs are violated.)

Note: at the moment, `SLOSlackDeploymentProcess` requires a non-open-source library, `slo_transcoder`, so this can only be used internally at Yelp.

You will need to list your available states in `states` and valid transitions from `valid_transitions`, and write code to handle these transitions in `on_enter_<state>` methods.

An example usage of Sticht is Paasta's [`mark-for-deployment`](https://github.com/Yelp/paasta/blob/master/paasta_tools/cli/cmds/mark_for_deployment.py).
