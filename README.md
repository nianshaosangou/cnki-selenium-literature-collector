# cnki-selenium-literature-collector
基于 Python 和 Selenium 的知网文献信息采集工具，支持关键词检索、自动翻页，并将题名、作者、来源、发表时间、摘要和关键词导出为 CSV；可在浏览器中手动登录或验证。/A Python/Selenium tool to search CNKI by keyword, navigate result pages automatically, and export titles, authors, sources, dates, abstracts, and keywords to CSV. Manual browser login/verification is supported.
# 知网 Selenium 文献信息采集工具

A Python and Selenium tool for collecting literature metadata from CNKI.

## 项目简介

本项目使用 Python 和 Selenium 自动打开知网检索页面，根据用户输入的关键词和目标数量采集文献信息。

程序支持：

- 输入关键词进行检索
- 自动翻页，直到达到目标数量或没有更多结果
- 采集题名、作者、来源、发表时间和文献类型
- 进入详情页采集摘要和关键词
- 自动去除重复记录
- 将结果保存为 CSV 文件
- 保存检查点，程序中断后可以保留已经采集的记录
- 遇到登录或安全验证时，允许用户在浏览器中手动处理

本项目不绕过验证码、登录限制或访问权限。使用时请遵守知网的服务条款、版权规定和学校或机构的相关要求。

## 环境要求

- Windows
- Python 3.10 或更高版本
- Microsoft Edge 浏览器
- Selenium 4.51.0 或更高版本

程序使用 Selenium Manager 管理 EdgeDriver，通常不需要手动下载驱动。

## 安装依赖

在项目文件夹中打开 PowerShell，执行：

```powershell
python -m pip install -r requirements.txt
