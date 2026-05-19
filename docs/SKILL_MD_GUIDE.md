# SKILL.md 说明文档

## 📋 什么是 SKILL.md？

**SKILL.md** 是一个遵循 GitHub Copilot 标准的技能定义文件，用于：

1. **描述项目专长** — 明确说明这个项目解决什么问题
2. **自动化工具发现** — 让 AI 助手和自动化工具知道何时使用此项目
3. **快速集成** — 提供标准化的安装、配置、使用说明
4. **生态集成** — 与其他 Copilot 工具和技能相互联动

## 📁 文件结构

### 前置定义（YAML）
```yaml
---
name: javlibrary-scraper                    # 技能的标识符
description: 技能的详细描述，包含触发关键词  # AI 用来判断是否需要该技能
---
```

### 主要内容

1. **简介** — 项目概述和核心特性
2. **对比表** — 与其他方案的优势
3. **性能指标** — 真实场景的性能数据
4. **安装指南** — 快速安装步骤
5. **快速开始** — 最常用的命令
6. **配置说明** — 环境变量和参数
7. **数据格式** — 输出数据结构
8. **浏览器配置** — 技术细节
9. **扩展性** — 适用场景和限制
10. **已知限制** — 边界条件
11. **故障排查** — 常见问题解决
12. **集成示例** — 代码使用示例
13. **文档链接** — 其他资源

## 🎯 使用场景

### 何时触发该技能

AI 助手会在以下请求时自动考虑使用这个技能：

✅ **直接请求：**
- "帮我爬取 JAVLibrary 的影片列表"
- "提取视频元数据"
- "爬虫 Cloudflare 网站"

✅ **间接请求：**
- "我需要一个网页爬虫"
- "如何处理 Cloudflare 验证？"
- "绕过机器人检测"

✅ **相关任务：**
- "批量下载视频封面"
- "提取网站数据到 CSV"
- "处理动态加载的内容"

### 何时不适用

❌ **需要人工交互的页面** — 登录、填表等
❌ **复杂的前端应用** — SPAs 可能需要特殊处理
❌ **实时性能要求** — 实时数据流
❌ **硬验证码** — 交互式 CAPTCHA

## 📊 SKILL.md 中的关键信息

| 部分 | 用途 | 示例 |
|------|------|------|
| `name` | 技能唯一标识 | `javlibrary-scraper` |
| `description` | 触发关键词和功能描述 | "scrape", "Cloudflare", "extract metadata" |
| Why section | 与替代方案对比 | 性能、功能、资源占用 |
| Quick start | 最常见的使用方式 | `uv run test_scraper.py` |
| Configuration | 环境设置 | 代理配置示例 |
| Troubleshooting | 常见问题 | 403 错误、代理问题 |
| Integration | 代码示例 | 如何在代码中使用 |

## 🔍 SKILL.md 的解析示例

**用户说：** "帮我爬取 JAVLibrary 最想要的影片列表"

**AI 流程：**
1. 检查 SKILL.md 的 `description` → 发现关键词匹配 ✓
2. 读取 `name` → 确定技能为 `javlibrary-scraper` ✓
3. 查看 "Quick start" 部分 → 推荐 `uv run test_scraper.py`
4. 建议用户使用该技能 ✓

**用户说：** "我遇到 403 错误"

**AI 流程：**
1. 在 SKILL.md 的 "Troubleshooting" 中找到 "403 Forbidden errors"
2. 给出诊断步骤：`uv run test_proxy.py`
3. 链接到 `TROUBLESHOOT_403.md`
4. 提供解决方案 ✓

## 🎬 SKILL.md 的实际应用

### 场景 1：第一次使用

```
用户："我想从 JAVLibrary 爬取影片"
AI 查阅 SKILL.md：
  → 查看 "Installation" 部分
  → 推荐 "Quick start" 命令
  → 用户成功运行爬虫
```

### 场景 2：遇到问题

```
用户："为什么收到 403 错误？"
AI 查阅 SKILL.md：
  → 找到 "Troubleshooting → 403 Forbidden"
  → 推荐运行诊断脚本
  → 说明如何更换代理
```

### 场景 3：集成到其他项目

```
用户："如何在我的项目中使用这个爬虫？"
AI 查阅 SKILL.md：
  → 显示 "Integration examples" 代码
  → 解释如何自定义配置
  → 提供完整的工作示例
```

## 🔧 SKILL.md 的维护

当项目更新时，应该更新 SKILL.md：

| 更新类型 | 需要更新的内容 |
|---------|--------------|
| 新功能 | `description`、`Quick start` |
| 性能改进 | "Why pick" 表格的数据 |
| 新依赖 | "Dependencies" 部分 |
| Bug 修复 | "Known limits" 部分 |
| 新配置选项 | "Configuration" 部分 |
| 新故障排查 | "Troubleshooting" 部分 |

## 📚 与其他文档的关系

```
SKILL.md (入口点，给 AI)
  ↓
  ├→ QUICKSTART.md (用户快速上手)
  ├→ HOWTO.md (详细使用指南)
  ├→ TROUBLESHOOT_403.md (特定问题)
  └→ FINAL_SUMMARY.md (项目概览)

README.md (项目介绍，给 GitHub)
```

**SKILL.md 的特点：**
- 🎯 **面向 AI** — 使用关键词和结构化信息
- 📋 **简洁清晰** — 快速查阅和解析
- 🔗 **链接丰富** — 引导到详细文档
- 🔄 **易于维护** — 标准化格式

## ✅ SKILL.md 检查清单

创建高质量的 SKILL.md 时应包含：

- [ ] 清晰的 `name` 标识符
- [ ] 包含关键词的 `description`
- [ ] "Why" 对比表（如果有竞品）
- [ ] 真实性能数据
- [ ] 简明的安装步骤
- [ ] 常用命令示例
- [ ] 配置选项说明
- [ ] 输出数据格式示例
- [ ] 已知限制明确列出
- [ ] 常见问题详细解答
- [ ] 代码集成示例
- [ ] 指向其他文档的链接
- [ ] 许可证信息

## 🚀 SKILL.md 在生态中的角色

### 对于 GitHub Copilot
- ✓ 自动推荐相关工具
- ✓ 在对话中快速查阅
- ✓ 提高回答质量和准确性

### 对于其他 AI 工具
- ✓ 通过标准格式识别技能
- ✓ 自动集成到工作流
- ✓ 提供规范化的使用信息

### 对于开发者社区
- ✓ 快速了解项目用途
- ✓ 标准化的文档格式
- ✓ 易于集成和扩展

## 📖 参考资源

- 示例来源：https://github.com/h4ckf0r0day/obscura
- Copilot 文档：https://docs.github.com/en/copilot
- SKILL 标准：遵循 GitHub 推荐的格式

---

**SKILL.md 是现代开发的标准，它让 AI 助手和自动化工具能够更高效地帮助开发者。** ✨
