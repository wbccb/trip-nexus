# 构建（私有化部署）

## 准备配置文件

找一个空目录，创建 docker-compose.yaml 文件：

```yaml
services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    restart: always
    networks:
      - searxng_net
    ports:
      - "8080:8080"
    volumes:
      - ./searxng:/etc/searxng
    environment:
      - SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml

networks:
  searxng_net:
```

## 针对项目调用的配置修改

在同目录下创建 searxng 文件夹，并在其中创建 settings.yml。必须包含以下内容以解决你遇到的报错：

```yaml
use_default_settings: true

server:
  # 必须设置密钥，随便写一段长字符串
  secret_key: "your_random_secret_key_here"
  # 允许 API 访问
  base_url: http://localhost:8080/
  # 解决 403/CORS 问题：允许所有来源访问（调试用）
  method: "GET"

search:
  # 开启 JSON 格式输出，这是项目调用的基础
  formats:
    - html
    - json

# 解决 429 报错：关闭内置的访问频率限制器
enabled_plugins:
  - 'Hash plugin'
  - 'Self-contained images'

# 重点：关闭限制器，防止本地调试被封
limiter: false
```


## 启动服务

```shell
docker-compose up -d
```