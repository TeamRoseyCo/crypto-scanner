export async function GET(request) {
	return new Response(JSON.stringify({ status: 'ok' }), {
		status: 200,
		headers: { 'Content-Type': 'application/json' },
	});
}

export async function POST(request) {
	return new Response(null, { status: 204 });
}
