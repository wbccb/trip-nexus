直接启动就行
```shell
# docker compose up -d
docker-compose up -d
```


一旦你的私有服务跑起来了（假设地址是 `http://localhost:8080`），你在项目中配置地址时请注意：

URL 格式： `http://localhost:8080/search?q=关键词&format=json`

并发控制：虽然你自己不限制自己了，但 Google/Bing 会限制你的服务器 IP => 如果请求太猛，虽然 SearXNG 不报错，但返回的结果里引擎部分会显示 "Timeout" 或 "Forbidden"。