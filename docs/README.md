# JavlibraryScrapy · 设计文档

> 项目设计文档索引。面向"想理解 / 改这个项目"的人。

## 目录

- [架构总览](#架构总览)
- [本地影片库模块](#本地影片库模块)
- [画廊刷新流程](#画廊刷新流程)
- [归档](#归档)
- [阅读路径建议](#阅读路径建议)

---

## 架构总览

📄 [`architecture.md`](architecture.md) — **系统级架构图**：爬虫 / 服务 / UI / 本地库的依赖关系、数据流向、模块边界。

- ASCII + Mermaid 双版本（GitHub 自动渲染）
- 包含 4 张图：系统总览、端到端 workflow、本地库数据流、画廊请求生命周期
- 与 `CLAUDE.md` 的事实保持一致；高层抽象，不重复实现细节

## 本地影片库模块

📄 [`library-feature.md`](library-feature.md) — 本地影片库扫描 + 画廊集成的设计稿。

- 11 节：目标 / 决策清单（27 条）/ 架构 / 数据模型 / 端点 / 配置 / 算法 / 文件改动 / 测试 / 风险 / 实现顺序
- 重点章节：第 7 节（关键算法）+ 第 2 节（决策理由）
- 适用人群：想改本地库扫描逻辑、加新元数据字段、改端点的人

## 画廊刷新流程

📄 [`refresh-flows.md`](refresh-flows.md) — 画廊 3 个刷新按钮的完整链路。

- 3 个按钮各自一张流程图：手动刷新 / 刷新库 / 单部 ↻
- 每个按钮覆盖：端点、状态机、输出文件、关键代码位置、排错要点
- 适用人群：调试抓取失败 / 想新增刷新入口的人

---

## 归档

📁 [`archive/`](archive/) — 历史开发文档，仅作考古参考：

| 文件 | 内容 |
| --- | --- |
| `SKILL.md` | 归档的 JAVLibrary-scraper skill 描述 |
| `SCRAPLING_COMPARISON.md` / `SCRAPLING_QUICK_START.md` | Scrapling 替代 Scrapy 的迁移记录 |
| `SCRAPY_MIGRATION_GUIDE.md` / `SCRAPY_SETUP_GUIDE.md` | 旧 Scrapy 实现（已在 `deprecated/`） |
| `TROUBLESHOOT_403.md` | JAVBus 图片 CDN 403 排查 |
| `HOWTO.md` / `QUICKSTART.md` / `JAVLIBRARY_SCRAPER_GUIDE.md` | 早期使用指南（已被 README 取代） |
| `FILES_CREATED.md` / `PROJECT_FILES.md` / `FINAL_SUMMARY.md` / `PROJECT_COMPLETION.md` / `SKILL_COMPLETION.md` / `SKILL_MD_GUIDE.md` | 项目交付记录 |

---

## 阅读路径建议

按下面顺序读，可在 ~30 分钟内建立项目的完整心智模型：

1. **根目录 [`README.md`](../README.md)**（或中文版 [`README.zh.md`](../README.zh.md)）—— 了解功能 + 快速开始
2. **[`CLAUDE.md`](../CLAUDE.md)** —— 项目事实源：架构 + 关键命令 + 配置项
3. **[`docs/architecture.md`](architecture.md)** —— 系统级架构图（先看图，再读说明）
4. **[`docs/refresh-flows.md`](refresh-flows.md)** —— 画廊后端 4 phase 流水线细节
5. **[`docs/library-feature.md`](library-feature.md)** —— 本地库模块的算法和决策
6. **源码** —— `src/javlibraryscrapy/server/` 是 FastAPI 服务的实现，按 `routes/` → `services/` 顺序读

> 💡 `CLAUDE.md` 是给 Claude Code 用的"事实源"（包含行号、commit 引用、内部细节），与设计文档互为补充：CLAUDE.md 说"是什么"，docs/ 说"为什么"。
