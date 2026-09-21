# Personal configuration / 个人配置入口

| What / 内容 | Where / 位置 |
|---|---|
| Empty credential template / 凭据空白模板 | [hypoloom.env.example](hypoloom.env.example) |
| Private credentials / 真实凭据 | `<checkout>/.local/credentials/hypoloom.env` (ignored, directory 0700 / file 0600) |
| English / 中文 Profile templates | [profiles/templates](../profiles/templates/) |
| Your topic Profiles / 本地课题 | `<checkout>/profiles/local/<name>/` (ignored) |
| Research state / 运行记录 | `<checkout>/state/<run-id>/` (ignored) |

The private `.local` directory is hidden in ordinary file listings. The template
above is public and contains no secrets. Copy it once into your private location;
never fill in the tracked template. The reader enforces mode 0600 and rejects
symlinks; it does not repair permissions or overwrite an existing file.

This dotenv file holds OpenAlex, Unpaywall and Telegram values. Model authentication
still comes from the selected environment or existing Claude settings. Research
datasets remain at their explicitly authorized paths. Nothing automatically sources
this file into your shell or forwards all entries to a model.

中文：`.local` 是隐藏目录，普通目录浏览可能看不到。上方模板可公开提交，真实值只
填写到私有副本；读取器强制检查 0600 权限，拒绝符号链接，不会自动修改权限或覆盖文件。
该文件集中存放文献、通知凭据和联系信息，不包含全部个人数据；模型认证与研究数据
各有独立来源。多人共用检出目录时，各自引用自己的私有绝对路径，不共享填写后的文件。

Show actual paths and permissions without displaying values / 查看位置和权限：

```bash
./.conda/envs/hypoloom/bin/python -B -m hypoloom.bootstrap paths --profile YOUR_PROFILE
```

Omit `--profile` for the default layout. Profile-declared external credential paths
are displayed separately from the suggested default. See [Setup](../SETUP.md),
[安装配置](../SETUP.zh-CN.md) and [Profile guide](../profiles/README.md).
