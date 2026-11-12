// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { RenderCard } from '../render/RenderCard';

const recipe = {
    kind: 'guide',
    guideType: 'recipe',
    title: 'Weeknight chicken',
    description: 'One pan, forty minutes.',
    notes: 'Leftovers keep three days.',
    servings: 2,
    items: ['2 tablespoons olive oil', '1 whole chicken', 'Salt to taste'],
    steps: ['Heat the oven: Set it to 425°F.', 'Roast 15 minutes, then baste.'],
};

const workout = {
    kind: 'guide',
    guideType: 'workout',
    title: 'Quick circuit',
    servings: null,
    items: ['Mat', 'Kettlebell'],
    steps: ['Push-ups: 3 sets of 12, rest 45 seconds'],
};

const itinerary = {
    kind: 'guide',
    guideType: 'itinerary',
    title: 'Two days in Lisbon',
    servings: null,
    items: ['Walking shoes'],
    steps: ['Day 1 - Lisbon: Alfama walk, wait 20 minutes for tram 28', 'Day 2 - Sintra'],
};

afterEach(() => {
    for (const key of Object.keys(localStorage)) if (key.startsWith('gaia:guide')) localStorage.removeItem(key);
});

describe('GuideCard', () => {
    it('scales amounts when servings go up', () => {
        render(<RenderCard render="guide" data={recipe} />);
        expect(screen.getByText('2 tablespoons')).toBeInTheDocument();
        expect(screen.getByText('2 servings')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', { name: 'More servings' }));

        expect(screen.getByText('3 servings')).toBeInTheDocument();
        expect(screen.getByText('3 tablespoons')).toBeInTheDocument();
        // Whole things stay whole, and lines with no amount never change.
        expect(screen.getByText('2', { selector: '.render-guide__qty' })).toBeInTheDocument();
        expect(screen.getByText('Salt to taste')).toBeInTheDocument();
    });

    it('converts units and oven temperatures together', () => {
        render(<RenderCard render="guide" data={recipe} />);
        fireEvent.click(screen.getByRole('button', { name: /Switch to Metric/ }));
        expect(screen.getByText('30 ml')).toBeInTheDocument();
        expect(screen.getByText(/Set it to 220°C\./)).toBeInTheDocument();
    });

    it('puts a timer chip after each duration', () => {
        render(<RenderCard render="guide" data={recipe} />);
        const chip = screen.getByRole('button', { name: 'Start 15 minutes timer' });
        expect(chip).toHaveTextContent('15:00');
        fireEvent.click(chip);
        expect(screen.getByRole('button', { name: 'Pause 15 minutes timer' })).toBeInTheDocument();
    });

    it('shows the unsupported card for a payload that breaks the shape', () => {
        render(<RenderCard render="guide" data={{ ...recipe, servings: 'four' }} />);
        expect(screen.getByText('Invalid guide payload')).toBeInTheDocument();
    });

    it('opens the guided view and closes it with Escape', () => {
        render(<RenderCard render="guide" data={recipe} />);
        fireEvent.click(screen.getByRole('button', { name: 'Start cooking' }));

        const dialog = screen.getByRole('dialog', { name: 'Weeknight chicken' });
        expect(within(dialog).getByText('Heat the oven')).toBeInTheDocument();
        expect(within(dialog).getByText('Step 1 of 2')).toBeInTheDocument();

        fireEvent.keyDown(document, { key: 'ArrowRight' });
        expect(within(dialog).getByText('Step 2 of 2')).toBeInTheDocument();
        expect(within(dialog).getByRole('timer')).toHaveTextContent('15:00');

        fireEvent.keyDown(document, { key: 'Escape' });
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('closes the guided view from the X', () => {
        render(<RenderCard render="guide" data={recipe} />);
        fireEvent.click(screen.getByRole('button', { name: 'Start cooking' }));
        fireEvent.click(screen.getByRole('button', { name: 'Close guided view' }));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('remembers checked equipment across renders', () => {
        const { unmount } = render(<RenderCard render="guide" data={workout} />);
        fireEvent.click(screen.getByRole('checkbox', { name: 'Mat' }));
        unmount();
        render(<RenderCard render="guide" data={workout} />);
        expect(screen.getByRole('checkbox', { name: 'Mat' })).toBeChecked();
        expect(screen.getByRole('checkbox', { name: 'Kettlebell' })).not.toBeChecked();
    });

    it('shows an itinerary as days on a timeline with a packing checklist', () => {
        render(<RenderCard render="guide" data={itinerary} />);
        expect(screen.getByText('Packing')).toBeInTheDocument();
        expect(screen.getByRole('checkbox', { name: 'Walking shoes' })).toBeInTheDocument();
        expect(screen.getByText('Day 1 - Lisbon')).toBeInTheDocument();
        expect(screen.getByText('Day 2')).toBeInTheDocument();
        // No timers, no start button, no step numbers.
        expect(screen.queryByRole('button', { name: /timer/ })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /^Start/ })).not.toBeInTheDocument();
        expect(document.querySelectorAll('.render-guide__step-number')).toHaveLength(0);
        expect(document.querySelectorAll('.render-guide__step-dot')).toHaveLength(2);
    });

    it('counts sets in the guided workout', () => {
        render(<RenderCard render="guide" data={workout} />);
        fireEvent.click(screen.getByRole('button', { name: 'Start workout' }));
        const dialog = screen.getByRole('dialog');
        expect(within(dialog).getByText('Set 1 of 3')).toBeInTheDocument();
        fireEvent.click(within(dialog).getByRole('button', { name: 'Set done' }));
        expect(within(dialog).getByText('Set 2 of 3')).toBeInTheDocument();
        // Finishing a set starts the rest.
        expect(within(dialog).getByRole('button', { name: 'Pause 45 seconds timer' })).toBeInTheDocument();
    });

    it('shows a one-time tip that dismissing hides on every card', () => {
        render(
            <>
                <RenderCard render="guide" data={recipe} />
                <RenderCard render="guide" data={workout} />
            </>,
        );
        expect(screen.getAllByRole('note')).toHaveLength(2);

        fireEvent.click(screen.getAllByLabelText('Dismiss tip')[0]);

        expect(screen.queryAllByRole('note')).toHaveLength(0);
        expect(localStorage.getItem('gaia:guide-tip-dismissed')).toBe('1');
    });

    it('drops the tip once a control it points at is used', () => {
        render(<RenderCard render="guide" data={recipe} />);
        expect(screen.getByRole('note')).toHaveTextContent('Change the servings');

        fireEvent.click(screen.getByLabelText('More servings'));

        expect(screen.queryByRole('note')).toBeNull();
    });

    it('shows no tip on a card with nothing hidden', () => {
        render(<RenderCard render="guide" data={itinerary} />);

        expect(screen.queryByRole('note')).toBeNull();
    });
});
