// رابط قصير لموقع Ryn عبر Cloudflare (مجاني): الرابط القصير بيضل ظاهر ومبيتحوّل للرابط الطويل.
const ORIGIN = "ryn-ai-1.onrender.com";   // رابط Render الحالي

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const myHost = url.host;
    url.protocol = "https:";
    url.hostname = ORIGIN;
    url.port = "";

    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.set("X-Forwarded-Host", myHost);
    const ip = request.headers.get("CF-Connecting-IP");
    if (ip) headers.set("X-Forwarded-For", ip);

    const hasBody = !["GET", "HEAD"].includes(request.method);
    const res = await fetch(url.toString(), {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      redirect: "manual",
    });

    const out = new Headers(res.headers);
    const loc = out.get("location");
    if (loc) out.set("location", loc.split(ORIGIN).join(myHost));
    return new Response(res.body, { status: res.status, statusText: res.statusText, headers: out });
  },
};
