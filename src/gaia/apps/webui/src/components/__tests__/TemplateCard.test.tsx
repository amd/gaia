// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Unit tests for TemplateCard component.
 *
 * Tests cover:
 * - Card rendering with template data
 * - Button interactions (View, Edit, Validate)
 * - Card click navigation
 * - Accessibility features
 * - Display formatting
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { TemplateCard } from '../templates/TemplateCard';

// Mock lucide-react icons
vi.mock('lucide-react', () => ({
  FileText: ({ size }: { size?: number }) => <svg data-testid="file-text-icon" width={size} />,
  Clock: ({ size }: { size?: number }) => <svg data-testid="clock-icon" width={size} />,
  Settings: ({ size }: { size?: number }) => <svg data-testid="settings-icon" width={size} />,
  ChevronRight: ({ size }: { size?: number }) => <svg data-testid="chevron-right-icon" width={size} />,
}));

// Sample template data
const sampleTemplate = {
  name: 'standard-pipeline',
  description: 'Standard pipeline template for most projects',
  quality_threshold: 0.90,
  max_iterations: 10,
  agent_categories: {
    planning: ['planner', 'architect'],
    development: ['senior-developer', 'developer'],
    quality: ['quality-reviewer', 'security-auditor'],
  },
  routing_rules: [
    {
      condition: "defect_type == 'security'",
      route_to: 'security-auditor',
      priority: 1,
      loop_back: true,
      guidance: 'Fix security issues first',
    },
  ],
  quality_weights: {
    code_quality: 0.25,
    requirements_coverage: 0.25,
    testing: 0.25,
    documentation: 0.15,
    best_practices: 0.10,
  },
};

const mockHandlers = {
  onView: vi.fn(),
  onEdit: vi.fn(),
  onValidate: vi.fn(),
};

describe('TemplateCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Basic Rendering', () => {
    it('should render template card with all required information', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      // Check template name
      expect(screen.getByRole('heading', { name: /standard-pipeline/i })).toBeInTheDocument();

      // Check description
      expect(screen.getByText(/Standard pipeline template/i)).toBeInTheDocument();

      // Check quality threshold (displayed as percentage)
      expect(screen.getByText(/90%/)).toBeInTheDocument();

      // Check max iterations
      expect(screen.getByText(/10 iters/i)).toBeInTheDocument();

      // Check category count (3 categories)
      expect(screen.getByText(/3 categories/i)).toBeInTheDocument();

      // Check routing rules count (1 rule)
      expect(screen.getByText(/1 rules/i)).toBeInTheDocument();
    });

    it('should render card as clickable button role', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const card = screen.getByRole('button');
      expect(card).toBeInTheDocument();
      expect(card).toHaveAttribute('tabindex', '0');
    });

    it('should display quality weights in card', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      // Check quality weights label
      expect(screen.getByText(/Quality weights:/i)).toBeInTheDocument();

      // Check first 3 weights are shown
      expect(screen.getByText(/code_quality: 25%/i)).toBeInTheDocument();
      expect(screen.getByText(/requirements_coverage: 25%/i)).toBeInTheDocument();
      expect(screen.getByText(/testing: 25%/i)).toBeInTheDocument();
    });

    it('should show "+N more" for more than 3 quality weights', () => {
      const templateWithMoreWeights = {
        ...sampleTemplate,
        quality_weights: {
          code_quality: 0.20,
          requirements_coverage: 0.20,
          testing: 0.20,
          documentation: 0.15,
          best_practices: 0.10,
          performance: 0.05,
          security: 0.10,
        },
      };

      render(<TemplateCard template={templateWithMoreWeights} {...mockHandlers} />);

      // Should show first 3 + "+4 more"
      expect(screen.getByText(/\+4 more/i)).toBeInTheDocument();
    });

    it('should hide quality weights section when empty', () => {
      const templateWithoutWeights = {
        ...sampleTemplate,
        quality_weights: {},
      };

      render(<TemplateCard template={templateWithoutWeights} {...mockHandlers} />);

      expect(screen.queryByText(/Quality weights:/i)).not.toBeInTheDocument();
    });

    it('should handle template without description', () => {
      const templateWithoutDescription = {
        ...sampleTemplate,
        description: '',
      };

      render(<TemplateCard template={templateWithoutDescription} {...mockHandlers} />);

      expect(screen.queryByTestId('template-card-description')).not.toBeInTheDocument();
    });
  });

  describe('Button Interactions', () => {
    it('should call onView when View button is clicked', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const viewButton = screen.getByRole('button', { name: /View/i });
      fireEvent.click(viewButton);

      expect(mockHandlers.onView).toHaveBeenCalledWith('standard-pipeline');
    });

    it('should call onEdit when Edit button is clicked', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const editButton = screen.getByRole('button', { name: /Edit/i });
      fireEvent.click(editButton);

      expect(mockHandlers.onEdit).toHaveBeenCalledWith('standard-pipeline');
    });

    it('should call onValidate when Validate button is clicked', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const validateButton = screen.getByRole('button', { name: /Validate/i });
      fireEvent.click(validateButton);

      expect(mockHandlers.onValidate).toHaveBeenCalledWith('standard-pipeline');
    });

    it('should stop propagation when action buttons are clicked', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const card = screen.getByRole('button');
      const viewButton = screen.getByRole('button', { name: /View/i });

      // Click the view button
      fireEvent.click(viewButton);

      // Card's onView should NOT be called (stopPropagation worked)
      // The view button has its own handler that stops propagation
      // We verify this by checking mockHandlers.onView was only called by button click
      expect(mockHandlers.onView).toHaveBeenCalledTimes(1);
    });
  });

  describe('Card Click Navigation', () => {
    it('should call onView when card is clicked', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const card = screen.getByRole('button');
      fireEvent.click(card);

      expect(mockHandlers.onView).toHaveBeenCalledWith('standard-pipeline');
    });

    it('should call onView when card is activated via keyboard', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const card = screen.getByRole('button');

      // Simulate Enter key press
      fireEvent.keyDown(card, { key: 'Enter', code: 'Enter' });
      fireEvent.click(card);

      expect(mockHandlers.onView).toHaveBeenCalledWith('standard-pipeline');
    });

    it('should call onView when card receives Enter key', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const card = screen.getByRole('button');
      fireEvent.keyDown(card, { key: 'Enter', code: 'Enter', bubbles: true });

      // Card click handler should be triggered
      expect(mockHandlers.onView).toHaveBeenCalled();
    });

    it('should call onView when card receives Space key', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const card = screen.getByRole('button');
      fireEvent.keyDown(card, { key: ' ', code: 'Space', bubbles: true });

      expect(mockHandlers.onView).toHaveBeenCalled();
    });
  });

  describe('Display Formatting', () => {
    it('should format quality threshold as percentage', () => {
      const template = {
        ...sampleTemplate,
        quality_threshold: 0.85,
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/85%/)).toBeInTheDocument();
    });

    it('should format quality weights as percentages', () => {
      const template = {
        ...sampleTemplate,
        quality_weights: {
          code_quality: 0.333,
        },
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      // Should round to nearest integer
      expect(screen.getByText(/33%/)).toBeInTheDocument();
    });

    it('should show "0 categories" when no agent categories', () => {
      const template = {
        ...sampleTemplate,
        agent_categories: {},
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/0 categories/i)).toBeInTheDocument();
    });

    it('should show "0 rules" when no routing rules', () => {
      const template = {
        ...sampleTemplate,
        routing_rules: [],
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/0 rules/i)).toBeInTheDocument();
    });

    it('should handle missing agent_categories gracefully', () => {
      const template = {
        ...sampleTemplate,
        agent_categories: undefined as unknown as Record<string, string[]>,
      };

      expect(() => {
        render(<TemplateCard template={template} {...mockHandlers} />);
      }).not.toThrow();
    });

    it('should handle missing routing_rules gracefully', () => {
      const template = {
        ...sampleTemplate,
        routing_rules: undefined as unknown as typeof sampleTemplate.routing_rules,
      };

      expect(() => {
        render(<TemplateCard template={template} {...mockHandlers} />);
      }).not.toThrow();
      expect(screen.getByText(/0 rules/i)).toBeInTheDocument();
    });
  });

  describe('Accessibility', () => {
    it('should have accessible name from template name', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const card = screen.getByRole('button');
      expect(card).toHaveAccessibleName();
    });

    it('should have View button with accessible name', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const viewButton = screen.getByRole('button', {
        name: /View template standard-pipeline/i,
      });
      expect(viewButton).toBeInTheDocument();
    });

    it('should have Edit button with accessible name', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const editButton = screen.getByRole('button', {
        name: /Edit template standard-pipeline/i,
      });
      expect(editButton).toBeInTheDocument();
    });

    it('should have Validate button with accessible name', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const validateButton = screen.getByRole('button', {
        name: /Validate template standard-pipeline/i,
      });
      expect(validateButton).toBeInTheDocument();
    });

    it('should be keyboard navigable', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const card = screen.getByRole('button');
      expect(card).toHaveAttribute('tabindex', '0');
    });

    it('should have proper button structure', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const buttons = screen.getAllByRole('button');
      // Card + View + Edit + Validate = 4 buttons
      expect(buttons).toHaveLength(4);
    });
  });

  describe('Edge Cases', () => {
    it('should handle template with very long name', () => {
      const template = {
        ...sampleTemplate,
        name: 'very-long-template-name-that-might-cause-layout-issues',
      };

      expect(() => {
        render(<TemplateCard template={template} {...mockHandlers} />);
      }).not.toThrow();

      expect(screen.getByText(/very-long-template-name/i)).toBeInTheDocument();
    });

    it('should handle template with very long description', () => {
      const template = {
        ...sampleTemplate,
        description:
          'This is a very long description that might wrap to multiple lines and test the card layout behavior with extended content that exceeds normal length expectations.',
      };

      expect(() => {
        render(<TemplateCard template={template} {...mockHandlers} />);
      }).not.toThrow();
    });

    it('should handle special characters in template name', () => {
      const template = {
        ...sampleTemplate,
        name: 'template-with-special-chars_v2.0-test',
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/template-with-special-chars/i)).toBeInTheDocument();
    });

    it('should handle quality threshold of 0', () => {
      const template = {
        ...sampleTemplate,
        quality_threshold: 0,
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/0%/)).toBeInTheDocument();
    });

    it('should handle quality threshold of 1 (100%)', () => {
      const template = {
        ...sampleTemplate,
        quality_threshold: 1.0,
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/100%/)).toBeInTheDocument();
    });

    it('should handle max_iterations of 1', () => {
      const template = {
        ...sampleTemplate,
        max_iterations: 1,
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/1 iters/i)).toBeInTheDocument();
    });

    it('should handle large max_iterations', () => {
      const template = {
        ...sampleTemplate,
        max_iterations: 100,
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/100 iters/i)).toBeInTheDocument();
    });

    it('should handle many agent categories', () => {
      const template = {
        ...sampleTemplate,
        agent_categories: {
          planning: ['planner'],
          analysis: ['analyst'],
          architecture: ['architect'],
          development: ['developer'],
          testing: ['tester'],
          review: ['reviewer'],
          security: ['security-auditor'],
          deployment: ['devops'],
          documentation: ['tech-writer'],
          management: ['project-manager'],
        },
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/10 categories/i)).toBeInTheDocument();
    });

    it('should handle many routing rules', () => {
      const template = {
        ...sampleTemplate,
        routing_rules: Array(15).fill({
          condition: "defect_type == 'testing'",
          route_to: 'tester',
          priority: 1,
          loop_back: false,
        }),
      };

      render(<TemplateCard template={template} {...mockHandlers} />);

      expect(screen.getByText(/15 rules/i)).toBeInTheDocument();
    });
  });

  describe('Card Layout Structure', () => {
    it('should have header section with icon and title', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      const header = screen.getByTestId('file-text-icon');
      expect(header).toBeInTheDocument();
    });

    it('should have body section with stats', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      // Check for stats elements
      expect(screen.getByText(/90%/)).toBeInTheDocument();
      expect(screen.getByText(/10 iters/i)).toBeInTheDocument();
      expect(screen.getByText(/3 categories/i)).toBeInTheDocument();
      expect(screen.getByText(/1 rules/i)).toBeInTheDocument();
    });

    it('should have footer section with action buttons', () => {
      render(<TemplateCard template={sampleTemplate} {...mockHandlers} />);

      expect(screen.getByRole('button', { name: /View/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Edit/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Validate/i })).toBeInTheDocument();
    });
  });
});

describe('TemplateCard with minimal template', () => {
  const minimalTemplate = {
    name: 'minimal',
    description: '',
    quality_threshold: 0.9,
    max_iterations: 10,
    agent_categories: {},
    routing_rules: [],
    quality_weights: {},
  };

  const mockHandlers = {
    onView: vi.fn(),
    onEdit: vi.fn(),
    onValidate: vi.fn(),
  };

  it('should render with minimal data', () => {
    expect(() => {
      render(<TemplateCard template={minimalTemplate} {...mockHandlers} />);
    }).not.toThrow();
  });

  it('should show zeros for empty collections', () => {
    render(<TemplateCard template={minimalTemplate} {...mockHandlers} />);

    expect(screen.getByText(/0 categories/i)).toBeInTheDocument();
    expect(screen.getByText(/0 rules/i)).toBeInTheDocument();
  });

  it('should not show quality weights section', () => {
    render(<TemplateCard template={minimalTemplate} {...mockHandlers} />);

    expect(screen.queryByText(/Quality weights:/i)).not.toBeInTheDocument();
  });
});
