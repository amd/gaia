// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { Image as NativeImage, View } from "react-native";

import { middleTruncate } from "@webui/utils/format";

import { useT } from "@/i18n/useT";
import { Icon, IconButton, Row, Stack, Text, useTheme } from "@/ui";
import { FileText, X } from "@/ui/icons";

import type { Attachments } from "./useAttachments";

/** One chip per attached file, with its upload state and a remove button. */
export function AttachmentChips({ attachments }: { attachments: Attachments }) {
  const t = useT();
  const { colors, radius } = useTheme();
  if (attachments.items.length === 0) return null;
  return (
    <View accessibilityRole="list" accessibilityLabel={t("composer.attached")}>
      <Stack gap="xs" testID="composer-attachments">
        {attachments.items.map((a) => (
          <View
            key={a.id}
            style={{
              borderRadius: radius.md,
              borderWidth: 1,
              borderColor:
                a.status === "failed" ? colors.danger : colors.borderLight,
              backgroundColor: colors.bgCard,
              paddingStart: 8,
            }}
          >
            <Row gap="sm" justify="between">
              <Row gap="sm" flex={1}>
                {a.isImage ? (
                  <NativeImage
                    source={{ uri: a.uri }}
                    style={{ width: 24, height: 24, borderRadius: radius.sm }}
                    accessibilityIgnoresInvertColors
                  />
                ) : (
                  <Icon icon={FileText} size="sm" tone="secondary" />
                )}
                <View style={{ flex: 1 }}>
                  <Text role="label" numberOfLines={1} compact dir="ltr">
                    {middleTruncate(a.name)}
                  </Text>
                  {a.status === "uploading" ? (
                    <Text role="caption" tone="secondary">
                      {t.bare("composer.uploading")}
                    </Text>
                  ) : a.status === "failed" ? (
                    <Text
                      role="caption"
                      tone="danger"
                      accessibilityRole="alert"
                    >
                      {a.error}
                    </Text>
                  ) : a.isMedia ? (
                    <Text role="caption" tone="secondary">
                      {t.bare("composer.mediaReady")}
                    </Text>
                  ) : null}
                </View>
              </Row>
              <IconButton
                icon={X}
                size="sm"
                label={t("composer.remove", { name: a.name })}
                onPress={() => attachments.remove(a.id)}
              />
            </Row>
          </View>
        ))}
      </Stack>
    </View>
  );
}
