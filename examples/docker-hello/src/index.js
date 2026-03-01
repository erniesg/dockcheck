const http = require("http");

const PORT = process.env.PORT || 3000;

const server = http.createServer((req, res) => {
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ message: "Hello from dockcheck!" }));
});

server.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
