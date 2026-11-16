// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState } from 'react';
import { useT } from '@/i18n/useT';
import { Brain, Check, ChevronRight, Copy, Plus, RotateCcw, Search, Send, Square, X } from '../icons';
import {
    Button,
    Card,
    Divider,
    Icon,
    IconButton,
    Ltr,
    Row,
    Screen,
    SmallButton,
    Spacer,
    Spinner,
    Stack,
    Surface,
    Text,
    TextField,
    Pressable,
} from '..';

const ROLES = ['caption', 'label', 'ui', 'body', 'prose', 'title', 'display'] as const;
const TONES = ['primary', 'secondary', 'muted', 'accent', 'danger', 'success', 'warning'] as const;

// Every label and sample text is an existing catalog key, so the Arabic shots show real Arabic.
export function UIGallery() {
    const t = useT();
    const [text, setText] = useState('');
    const [long, setLong] = useState(t('settings.privacy.deleteWarning'));
    const [pressed, setPressed] = useState(0);
    const sample = t('settings.shortcut.send');
    const longLabel = t('settings.errors.notInstalledTitle');
    const press = () => setPressed((n) => n + 1);

    return (
        <Screen scroll keyboard="avoid">
            <Stack gap="xl" padding="lg">
                <Stack gap="sm">
                    {ROLES.map((role) => (
                        <Text key={role} role={role}>
                            {t('settings.privacy.deleteTitle')}
                        </Text>
                    ))}
                    <Text role="prose">{t('settings.privacy.deleteWarning')}</Text>
                    <Row gap="sm" wrap>
                        {TONES.map((tone) => (
                            <Text key={tone} tone={tone} weight="medium">
                                {t('settings.messaging.stop')}
                            </Text>
                        ))}
                    </Row>
                    <Row gap="sm">
                        <Text mono>{t('settings.messaging.copy')}</Text>
                        <Text italic>{t('settings.messaging.retry')}</Text>
                        <Text underline tone="accent">{t('settings.messaging.retry')}</Text>
                        <Text weight="bold" selectable>
                            {t('settings.shortcut.newChat')}
                            <Text tone="accent"> {t('settings.shortcut.search')}</Text>
                        </Text>
                    </Row>
                    <Text>
                        {t('settings.privacy.deleteFailed', { n: 3, reason: t('settings.advanced.saveFailed') })}{' '}
                        <Ltr>{t.locale}</Ltr>
                    </Text>
                </Stack>

                <Divider />

                <Stack gap="md">
                    <Button variant="primary" label={t('settings.advanced.save')} onPress={press} icon={Check} />
                    <Button label={t('settings.messaging.cancel')} onPress={press} />
                    <Button
                        variant="ghost"
                        label={t('settings.skills.search')}
                        onPress={press}
                        icon={Search}
                        iconEnd={ChevronRight}
                    />
                    <Button variant="danger" label={t('settings.privacy.delete')} onPress={press} />
                    <Row gap="sm" wrap>
                        <Button size="sm" label={t('settings.messaging.cancel')} onPress={press} />
                        <Button variant="primary" size="sm" label={t('settings.advanced.save')} onPress={press} />
                        <Button variant="danger" size="sm" label={t('settings.privacy.delete')} onPress={press} />
                        <SmallButton variant="danger" label={t('settings.privacy.delete')} onPress={press} />
                        <SmallButton variant="accent" label={t('settings.shortcut.newChat')} icon={Plus} onPress={press} />
                    </Row>
                    <Button variant="primary" label={t('settings.advanced.loading')} busy onPress={press} />
                    <Button label={t('settings.messaging.cancel')} disabled onPress={press} />
                    <Button full variant="secondary" label={longLabel} onPress={press} icon={RotateCcw} />
                    <Text role="caption" tone="muted">
                        {pressed}
                    </Text>
                </Stack>

                <Row gap="sm" wrap>
                    <IconButton icon={Send} label={t('settings.shortcut.send')} variant="primary" onPress={press} />
                    <IconButton icon={Square} label={t('settings.shortcut.stop')} variant="filled" onPress={press} />
                    <IconButton icon={Copy} label={t('settings.messaging.copy')} onPress={press} />
                    <IconButton icon={X} label={t('settings.close')} variant="danger" onPress={press} />
                    <IconButton icon={Plus} label={t('settings.shortcut.newChat')} size="sm" onPress={press} />
                    <IconButton icon={Plus} label={t('settings.advanced.loading')} busy />
                    <IconButton icon={Plus} label={t('settings.shortcut.newChat')} disabled />
                    <Icon icon={Brain} size="sm" tone="accent" />
                    <Icon icon={ChevronRight} size="lg" />
                    <Spinner />
                </Row>

                <Stack gap="md">
                    <Card>
                        <Text>{sample}</Text>
                    </Card>
                    <Card variant="raised">
                        <Text>{sample}</Text>
                    </Card>
                    <Surface tone="card" radius="lg" padding="lg" border elevation="sm">
                        <Text>{sample}</Text>
                    </Surface>
                    <Surface tone="recessed" radius="md" padding="md">
                        <Text mono role="caption">
                            {sample}
                        </Text>
                    </Surface>
                    <Surface tone="userMsg" radius="xl" padding="md" elevation="md">
                        <Text>{sample}</Text>
                    </Surface>
                    <Card onPress={press}>
                        <Row gap="md">
                            <Text weight="medium">{t('settings.privacy.deleteTitle')}</Text>
                            <Spacer />
                            <Icon icon={ChevronRight} />
                        </Row>
                    </Card>
                    <Pressable onPress={press} accessibilityLabel={sample}>
                        <Text tone="accent">{sample}</Text>
                    </Pressable>
                </Stack>

                <Stack gap="md">
                    <TextField
                        label={t('settings.skills.search')}
                        value={text}
                        onChangeText={setText}
                        placeholder={t('settings.skills.search')}
                        hint={t('settings.shortcut.search')}
                    />
                    <TextField
                        label={t('settings.advanced.save')}
                        value={text}
                        onChangeText={setText}
                        error={t('settings.advanced.saveFailed')}
                    />
                    <TextField
                        label={t('settings.privacy.deleteTitle')}
                        value={long}
                        onChangeText={setLong}
                        multiline
                        maxLines={4}
                    />
                    <TextField label={t('settings.messaging.cancel')} value={text} onChangeText={setText} disabled />
                </Stack>
            </Stack>
        </Screen>
    );
}
