#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import inspect
from typing import (
    TypeVar,
)

import synchronicity

synchronizer = synchronicity.Synchronizer()


def synchronize_api(obj, target_module=None):
    if inspect.isclass(obj) or inspect.isfunction(obj):
        blocking_name = obj.__name__.lstrip("_")
    elif isinstance(obj, TypeVar):
        blocking_name = "_BLOCKING_" + obj.__name__
    else:
        blocking_name = None
    if target_module is None:
        target_module = obj.__module__
    return synchronizer.create_blocking(obj, blocking_name, target_module=target_module)
