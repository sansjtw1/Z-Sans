# Z-Sans Asset Breeding Engine  
<p align="center">  
    <img src="images/logo.png" alt="Logo">  
  </a>  
  <h3 align="center">Z-Sans</h3>  
  <p align="center">  
    🔥 "Automated Asset Collection System Based on Asset Breeding Engine"  
    <br />  
    <a href="https://opensource.org/licenses/MIT"><img alt="License" src="https://img.shields.io/badge/License-MIT-yellow.svg"/></a>  
    <a href="https://www.python.org/downloads/release/python-390/"><img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9+-blue.svg"/></a>  
    <a href="https://github.com/sansjtw1/Z-Sans/issues"><img alt="GitHub issues" src="https://img.shields.io/github/issues/sansjtw1/Z-Sans"/></a>  
    <br>
    <a href="README.md">README</a> | <a href="README_CN.md">中文文档</a>
  </p>  

## 🚀 Project Overview  
Z-Sans is a powerful cybersecurity tool featuring an **Asset Breeding Engine**, helping security teams rapidly identify and map an organization's complete attack surface. With minimal seed assets (domains or URLs), Z-Sans automatically discovers related assets such as domains, IPs, URLs, ports, and JavaScript resources while providing detailed asset reports.  

## ✨ Key Features  
- **Multi-Type Asset Support**: Domains, IPs, URLs, ports, and JS files  
- **Configurable Scanning Strategies**: Priority-based scanning with customizable depth and concurrency control  
- **Rich Tool Integration**: Supports subfinder, naabu, subfinder and other external tools  
- **Flexible Output Formats**: Supports JSON, CSV, GraphML, HTML, and other formats  
- **Internationalization**: Built-in Chinese and English language support  
- **Detailed Logging System**: Supports multi-level log output for debugging and monitoring  
- **Modular Design**: Core functionality decoupled from tool implementation for easy extension  
- **Lightweight Tool**: Focused on asset topology discovery  
- **Asset Breeding Core**: Engine automatically generates new assets from discovered relationships  

## 🛠️ Project Structure  
```  
Z-Sans/  
├── assets/             # External tool scripts  
├── core/               # Core engine code  
│   ├── breeders/       # Core breeding algorithms  
│   ├── tools/          # Tool wrappers  
│   ├── i18n.py         # Internationalization  
│   ├── output.py       # Output processing  
│   └── zsans_engine.py # Breeding engine core  
├── i18n/               # Language resources  
├── output/             # Output directory  
├── templates/          # Report templates  
├── breeding-config.yaml # Configuration  
├── main.py             # Entry point  
├── README.md           # Documentation  
├── README_CN.md        # Chinese documentation  
└── requirements.txt    # Dependencies  
```  

## 💡 Installation Guide  
### Requirements  
- Python 3.9+  
- Supported OS: Windows, Linux, macOS  

### Installation Steps  
1. Clone repository  
```bash  
git clone https://github.com/sansjtw1/Z-Sans.git  
cd Z-Sans  
```  

2. Install dependencies  
```bash  
pip install -r requirements.txt  
```  

3. Configure tools (Optional)  
Modify `breeding-config.yaml` to set paths for tools like subfinder and naabu.  

## 📋 Usage  
> Note: Large enterprises with extensive assets may require longer breeding cycles.  

### Basic Commands  
```bash  
# Run with default configuration  
python main.py -d example.com  

# Start from URL seed  
python main.py -u https://example.com  

# Custom configuration  
python main.py -c your-config.yaml -d example.com  
```  

### CLI Arguments  
```  
--depth          Set breeding depth (asset generation cycles)  
--output         Custom output directory  
--verbose        Detailed debug output  
```  

## 💻 Configuration  
```yaml  
# Asset breeding constraints  
resource_limits:  
  max_domains: 2000    
  max_breeding_cycles: 5  # Max asset generation iterations  

# Breeding algorithms  
breeding_strategy: priority_expansion  
```  

## 📃 Disclaimer  
**Important Notice:**  
1. **Authorized Use Only**  
The Asset Breeding Engine may only operate on explicitly authorized targets  
2. **Compliance Responsibility**  
Users must ensure all breeding activities comply with local regulations  

## 📞 Contact  
sansjtw@163.com  