# Git development workflow

本项目采用两个独立仓库、同一发布版本号：backend_ae_tool 和 frontend_ae_tool。将它们克隆为同级 backend/、frontend/ 目录；v1.0.0 是首个配对基线。主线为 main，发布标签一旦推送不得移动或覆盖。

## 日常开发

每项需求从最新 main 建立独立短期分支，不在 main 或历史交付分支继续开发：

```bash
git switch main
git pull --ff-only origin main
git switch -c feature/short-description
# 修复使用 fix/short-description，文档使用 docs/short-description。
```

提交使用 feat:、fix:、docs:、test:、chore: 等前缀；只暂存与当前任务相关的文件。推送分支并创建面向 main 的 Pull Request，说明行为变化、验证结果及跨仓库依赖。先完成审查和必要检查，再使用 merge commit 合并并删除已合并的临时分支；禁止强推 main 或覆盖已发布标签。个人未合并分支需要变基/改写时先确认影响。

跨仓库的契约变更应在两侧分别建分支，并在两个 PR 中互相链接；不能只合并生成文件的一侧。PR 模板记录成对版本和验证范围。日常从 main 开发；重现首版或为首版单独维护补丁时可从 v1.0.0 建立 fix/v1-short-description。

## 验证与发布

验证命令见 README 的开发检查章节。CPU/浏览器回归先通过；改动仿真、媒体或同步路径时，补真实 SDK/UI 验收并保存证据。没有执行的环境不能写成已通过。SDK、机器人资产、真机日志、录像、venv 和本地密钥不入 Git。

准备发布时更新应用版本和 docs/releases/vX.Y.Z.md，在两个仓库完成审查与合并；确认 main 工作区干净且检查通过，再建立带说明的标签并显式推送。不要使用 git push --tags，把历史实验标签一起发布。

```bash
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin main
git push origin refs/tags/vX.Y.Z
```

推送版本标签后，Publish release 工作流核对该提交属于 main，并读取同一提交的发布说明创建 GitHub Release。它只发布源码版本，不部署服务、不上传本地数据，也不替代发布前测试；已存在的 Release 不会自动覆盖。脚本使用 GitHub Actions 内置令牌，无需保存个人访问令牌。参见 [gh release create](https://cli.github.com/manual/gh_release_create)。

首次发布时远端为空，因此从原始首个提交建立 main，再将既有 feature/p1-contracts 通过 --no-ff 合并，完整保留开发历史。本次用户明确授权首次本地审查、合并和推送；后续工作使用 PR 流程。历史 t18-local-20261102 标签保持原位置。仓库内规范和模板不等于 GitHub 服务端已启用分支保护或必需检查；配置这些规则需要仓库管理端操作。
