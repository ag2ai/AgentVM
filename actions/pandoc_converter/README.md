# Pandoc Converter Action

This action provides a simple interface to convert markdown files to DOCX (Microsoft Word) or other formats using pandoc.

## Usage

```bash
pandoc_converter <input_file> <output_file> [--format <format>]
```

### Arguments

- `input_file`: Path to the input markdown file (required)
- `output_file`: Path to the output file (required)
- `--format`: Output format (optional, defaults to format inferred from output file extension)

### Examples

Convert markdown to docx:
```bash
pandoc_converter document.md document.docx
```

Convert markdown to PDF:
```bash
pandoc_converter document.md document.pdf --format pdf
```

Convert markdown to HTML:
```bash
pandoc_converter document.md document.html --format html
```

## Requirements

- pandoc must be installed on the system
- The tool will check for pandoc installation and provide helpful error messages if not found

## Installation

To install pandoc:
- Ubuntu/Debian: `sudo apt-get install pandoc`
- macOS: `brew install pandoc`
- For other systems, see: https://pandoc.org/installing.html

## Features

- Automatic path resolution (supports both relative and absolute paths)
- Creates output directories if they don't exist
- Clear error messages for common issues
- Timeout protection (60 seconds)
- Validates input file existence and readability
