# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""UI services package."""

from .template_service import TemplateService, TemplateValidationError

__all__ = ["TemplateService", "TemplateValidationError"]
