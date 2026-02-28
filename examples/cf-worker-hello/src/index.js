export default {
  async fetch(request) {
    return new Response("Hello World from dockcheck!", {
      headers: { "content-type": "text/plain" },
    });
  },
};
