# HTML Templates

This directory contains HTML templates for the GAIA summarizer output.

## Files

### summary_report.html
The main template for summary reports. Features:
- Responsive design with gradient background
- Clean, professional layout
- Interactive elements (collapsible original content)
- Performance metrics visualization
- Proper formatting for participants and action items

## Template Structure

The template uses a placeholder `{{JSON_DATA}}` which gets replaced with the actual summary JSON data when generating the HTML file.

## Customization

To customize the appearance:
1. Edit the CSS styles in the `<style>` section
2. Modify the HTML structure as needed
3. Update the JavaScript formatting functions

The template is self-contained with no external dependencies, making it work offline and easy to share.

## Colors and Styling

- Primary gradient: `#667eea` to `#764ba2`
- Action items: Light blue background (`#f0f9ff`) with green accent (`#10b981`)
- Participants: Light orange background (`#fef3f2`) with amber accent (`#f59e0b`)
- Performance metrics: Yellow background (`#fef5e7`)

## JavaScript Functions

- `formatSummary(data)`: Main function to render the JSON data
- `escapeHtml(text)`: Safely escape HTML characters
- `formatActionItem(item)`: Format action items with metadata
- `toggleOriginal()`: Show/hide original content section