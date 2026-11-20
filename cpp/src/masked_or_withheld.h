// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#include <exception>
#include <optional>
#include <utility>

#include "gaia/types.h"

namespace gaia::detail {

/// @p value through @p mask, or nothing when masking throws: the caller then withholds the
/// value rather than send it unmasked.
template <class Mask>
std::optional<json> maskedOrWithheld(Mask&& mask, json value) {
    try {
        return std::forward<Mask>(mask)(std::move(value));
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

}  // namespace gaia::detail
