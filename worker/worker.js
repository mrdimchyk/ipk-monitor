export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = {
      'Access-Control-Allow-Origin': env.ALLOWED_ORIGIN || '*',
      'Access-Control-Allow-Headers': 'content-type',
      'Access-Control-Allow-Methods': 'POST,OPTIONS',
    };
    if (request.method === 'OPTIONS') return new Response('', {headers:cors});
    if (url.pathname !== '/subscribe' || request.method !== 'POST') return new Response('Not found', {status:404,headers:cors});
    try {
      const body = await request.json();
      const email = String(body.email || '').trim().toLowerCase();
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return new Response(JSON.stringify({error:'invalid_email'}), {status:400,headers:{...cors,'content-type':'application/json'}});
      const token = crypto.randomUUID();
      await env.DB.prepare('INSERT INTO subscribers (email, verified, active, token, created_at) VALUES (?, 0, 0, ?, datetime(\'now\')) ON CONFLICT(email) DO UPDATE SET token=excluded.token').bind(email, token).run();
      // Email confirmation is deliberately a separate step: configure a transactional sender before enabling it.
      return new Response(JSON.stringify({ok:true, confirmation_pending:true}), {headers:{...cors,'content-type':'application/json'}});
    } catch (e) {
      return new Response(JSON.stringify({error:'bad_request'}), {status:400,headers:{...cors,'content-type':'application/json'}});
    }
  }
};