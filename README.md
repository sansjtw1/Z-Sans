# Z-Sans Asset Breeding Engine

<p align="center">
    <img src="images/logo.png" alt="Z-Sans Logo">
    <h3 align="center">Z-Sans</h3>
    <p align="center">
        🔥 "Automated Asset Collection System Powered by Asset Breeding Engine"
        <br />
        <a href="https://opensource.org/licenses/MIT"><img alt="License" src="https://img.shields.io/badge/License-MIT-yellow.svg"/></a>
        <a href="https://www.python.org/downloads/release/python-390/"><img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9+-blue.svg"/></a>
        <a href="https://github.com/sansjtw1/Z-Sans/issues"><img alt="GitHub issues" src="https://img.shields.io/github/issues/sansjtw1/Z-Sans"/></a>
        <br>
        <a href="README.md">README</a> | <a href="README_CN.md">中文文档</a>
    </p>
</p>

## 🚀 Project Overview

Z-Sans is a powerful cybersecurity tool featuring an innovative **Asset Breeding Engine** that automates attack surface discovery and mapping. Starting with minimal seed assets (domains or URLs), Z-Sans systematically discovers and expands digital assets including domains, IPs, URLs, ports, and JavaScript resources while generating comprehensive asset reports.

## ✨ Key Features

- **Multi-Asset Support**: Domains, IPs, URLs, ports, and JS files
- **Configurable Breeding Strategies**: Priority-based scanning with depth customization
- **Tool Integration**: Supports subfinder, naabu, subfinder and other security tools
- **Flexible Output Formats**: JSON, CSV, GraphML and HTML reports
- **Internationalization**: Built-in Chinese and English support
- **Detailed Logging**: Multi-level logging for debugging
- **Modular Architecture**: Core engine decoupled from tool implementations
- **Lightweight Design**: Optimized for efficient resource usage
- **Asset Breeding Engine**: Automatically generates new assets from discovered relationships

## 🛠️ Project Structure

```
Z-Sans/
├── assets/             # External tool scripts
├── core/               # Core engine
│   ├── breeders/       # Breeding algorithms
│   ├── tools/          # Tool integrations
│   ├── i18n.py         # Internationalization
│   ├── output.py       # Output handlers
│   └── zsans_engine.py # Breeding engine core
├── i18n/               # Language resources
├── output/             # Results directory
├── templates/          # Report templates
├── breeding-config.yaml # Configuration
├── main.py             # Entry point
├── README.md           # This documentation (English)
├── README_CN.md        # Chinese documentation
└── requirements.txt    # Dependencies
```

## 💡 Installation Guide

### Requirements
- Python 3.9+
- Supported OS: Windows, Linux, macOS

### Installation Steps

1. Clone repository:
```bash
git clone https://github.com/sansjtw1/Z-Sans.git
cd Z-Sans
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure external tools (Optional):
Edit `breeding-config.yaml` to set paths for subfinder, naabu, etc.

## 📋 Usage

> Note: Large enterprises with extensive assets may require longer breeding cycles

### Basic Commands
```bash
# Run with domain seed
python main.py -d example.com

# Start from URL seed
python main.py -u https://example.com

# Custom configuration
python main.py -c your-config.yaml -d example.com

# Verbose output
python main.py -d example.com -v

# Custom output directory
python main.py -d example.com -o custom-output
```

### Command Line Arguments
```
-c, --config     Config file path (default: breeding-config.yaml)
-d, --domain     Add domain seeds (multiple supported)
-u, --url        Add URL seeds (multiple supported)
-o, --output     Output directory (default: output)
-v, --verbose    Verbose output mode
--init           Create default configuration
--version        Show version info
--depth          Set maximum breeding depth
```

## 💻 Configuration

Configuration file `breeding-config.yaml` includes:

### Breeding Strategy
```yaml
strategy: priority_based  # Priority-based asset breeding
```

### Language Configuration
```yaml
language:
  default_language: en    # Default language (en or zh_CN)
  supported_languages:   # Supported languages
    - en
    - zh_CN
  locale_dir: i18n        # Localization directory
```

### Asset Scope
```yaml
asset_scope:
  restrict_to_seed_domains: true    # Limit to seed domains
  restrict_to_seed_ip_ranges: false # Don't limit to seed IPs
  include_subdomains: true          # Include subdomains
  include_ip_ranges: true           # Include IP ranges
```

### Concurrency Control
```yaml
concurrency:
  max_tasks: 20        # Max concurrent processes
  tools:               # Tool-specific concurrency
    subfinder: 2       # Subdomain discovery
    naabu: 2           # Port scanning
    jsfinder: 2        # JS file discovery
```

### Resource Limits
```yaml
resource_limits:
  max_domains: 2000    # Maximum domains
  max_ips: 2000        # Maximum IPs
  max_urls: 5000       # Maximum URLs
  max_ports: 5000      # Maximum ports
  max_js: 2000         # Maximum JS files
```

## 🎯 Output Samples

Results include multiple formats in the output directory:
- JSON asset data
- CSV assets/relationships
- GraphML relationship diagrams
- HTML reports

![Output Sample](images/output1.png)

## 📃 Disclaimer

**Important:**
1. **Authorized Use Only**  
   Only operate on explicitly authorized targets
2. **Legal Compliance**  
   Users must comply with all applicable laws
3. **Ethical Operation**  
   Follow responsible disclosure practices
4. **No Liability**  
   Developers assume no liability for damages

## 🎈 Contributing

We welcome contributions! Please follow these steps:
1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Open a pull request

## 📃 License

Licensed under MIT - See LICENSE for details.

## 📞 Contact
Email: sansjtw@163.com  
GitHub: https://github.com/sansjtw1
Telegram: https://t.me/sansjtw