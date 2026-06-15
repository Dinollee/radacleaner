// radacleaner Worker API — Cloudflare Worker для REST API + D1

function json(data, status, cacheSeconds) {
	status = status || 200;
	const headers = {
		'Content-Type': 'application/json',
		'Access-Control-Allow-Origin': '*',
		'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
		'Access-Control-Allow-Headers': 'Content-Type, Authorization',
	};
	if (cacheSeconds) {
		headers['Cache-Control'] = `public, max-age=${cacheSeconds}`;
	}
	return new Response(JSON.stringify(data), { status, headers });
}

function error(msg, status) {
	return json({ error: msg }, status || 400);
}

export default {
	async fetch(request, env) {
		const url = new URL(request.url);
		const pathname = url.pathname;
		const method = request.method;

		// CORS
		if (method === 'OPTIONS') {
			return new Response(null, {
				headers: {
					'Access-Control-Allow-Origin': '*',
					'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
					'Access-Control-Allow-Headers': 'Content-Type, Authorization',
				},
			});
		}

		try {
			// --- STATUSES (for filter dropdown) ---
			if (method === 'GET' && pathname === '/api/statuses') {
				const { results } = await env.radacleaner_db.prepare(
					'SELECT current_status, COUNT(*) as count FROM bills WHERE current_status IS NOT NULL AND current_status != "" GROUP BY current_status ORDER BY count DESC'
				).all();
				return json({ statuses: results }, 200, 300);
			}

			// --- BY STAGE (for dashboard quick filter) ---
			if (method === 'GET' && pathname === '/api/by-stage') {
				const { results } = await env.radacleaner_db.prepare(
					'SELECT stage, current_status, COUNT(*) as count FROM bills GROUP BY stage, current_status ORDER BY stage, count DESC'
				).all();
				return json({ data: results }, 200, 300);
			}

			// --- STATS ---
			if (method === 'GET' && pathname === '/api/stats') {
				const [totalBills, byStage, highRisk, recentChanges, totalVotes, totalMps, activeMps, recentSync, analyzedCount] =
					await Promise.all([
						db(env, 'SELECT COUNT(*) as count FROM bills'),
						db(env, 'SELECT stage, COUNT(*) as count FROM bills WHERE stage IS NOT NULL GROUP BY stage ORDER BY stage'),
						db(env, "SELECT COUNT(*) as count FROM risk_assessments WHERE overall_score >= 50"),
						db(env, "SELECT COUNT(*) as count FROM change_log WHERE created_at > datetime('now', '-7 days')"),
						db(env, 'SELECT COUNT(*) as count FROM votes'),
						db(env, 'SELECT COUNT(*) as count FROM mps'),
						db(env, "SELECT COUNT(*) as count FROM mps WHERE end_date IS NULL OR end_date = ''"),
						db(env, 'SELECT * FROM sync_state ORDER BY last_checked DESC LIMIT 1', 'first'),
						db(env, 'SELECT COUNT(DISTINCT bill_id) as count FROM risk_assessments'),
					]);

				return json({
					totalBills: totalBills?.[0]?.count || 0,
					byStage: byStage || [],
					highRiskBills: highRisk?.[0]?.count || 0,
					recentChanges: recentChanges?.[0]?.count || 0,
					totalVotes: totalVotes?.[0]?.count || 0,
					totalMps: totalMps?.[0]?.count || 0,
					activeMps: activeMps?.[0]?.count || 0,
					lastSync: recentSync?.last_checked || null,
					analyzedBills: analyzedCount?.[0]?.count || 0,
				});
			}

			// --- BILLS LIST ---
			if (method === 'GET' && pathname === '/api/bills') {
				const limit = Math.min(Number(url.searchParams.get('limit')) || 50, 200);
				const offset = Number(url.searchParams.get('offset')) || 0;
				const stage = url.searchParams.get('stage');
				const status = url.searchParams.get('status');
				const search = url.searchParams.get('search');
				const sort = url.searchParams.get('sort') || 'status_changed_at';
				const order = (url.searchParams.get('order') || 'DESC').toUpperCase() === 'ASC' ? 'ASC' : 'DESC';
				const updatedAfter = url.searchParams.get('updated_after');
				const updatedBefore = url.searchParams.get('updated_before');
				const analyzed = url.searchParams.get('analyzed');

			// Use FTS5 for search — indexes bill_number, title, act_number
			let useFts = false;
			let ftsQuery = '';
			if (search && search.trim()) {
				// Sanitize: strip chars that unicode61 tokenizer treats as separators (dashes etc)
				const terms = search.trim().split(/\s+/)
					.map(t => t.replace(/['"<>()[\]{}\\:^#@!&;,.?=/\-]/g, '').trim())
					.filter(t => t.length > 0)
					.map(t => `"${t}"*`)
					.join(' OR ');
				if (terms) {
					ftsQuery = terms;
					useFts = true;
				}
			}

				const safeSort = ['created_at','updated_at','status_changed_at','registration_date','bill_number','stage','current_status','act_date'].includes(sort) ? sort : 'status_changed_at';
				const params = [];

				if (useFts) {
					// FTS5 search: join with bills_fts for fast full-text search
					let query = `SELECT b.id, b.bill_number, b.title, b.current_status, b.registration_date, b.committee, b.stage, b.updated_at, b.status_changed_at, ra.has_analysis,
						CASE WHEN ra.json_data LIKE '%"risk_level": "high"%' THEN 'high'
						     WHEN ra.json_data LIKE '%"risk_level": "medium"%' THEN 'medium'
						     WHEN ra.json_data LIKE '%"risk_level": "low"%' THEN 'low'
						     ELSE NULL END as risk_level
						FROM bills_fts fts
						JOIN bills b ON b.id = fts.rowid
						LEFT JOIN (SELECT bill_id, 1 as has_analysis, json_data FROM risk_assessments) ra ON ra.bill_id = b.id
						WHERE bills_fts MATCH ?`;
					params.push(ftsQuery);

					if (stage) { query += ' AND b.stage = ?'; params.push(Number(stage)); }
					if (status) { query += ' AND b.current_status = ?'; params.push(status); }
					if (updatedAfter) { query += ' AND b.status_changed_at >= ?'; params.push(updatedAfter); }
					if (updatedBefore) { query += ' AND b.status_changed_at <= ?'; params.push(updatedBefore + ' 23:59:59'); }
					if (analyzed === '1') { query += ' AND EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)'; }
					if (analyzed === '0') { query += ' AND NOT EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)'; }

					query += ` ORDER BY b.${safeSort} ${order} LIMIT ? OFFSET ?`;
					params.push(limit, offset);

					const { results } = await env.radacleaner_db.prepare(query).bind(...params).all();

					// COUNT with FTS5
					let countQuery = `SELECT COUNT(*) as total FROM bills_fts fts JOIN bills b ON b.id = fts.rowid WHERE bills_fts MATCH ?`;
					const countParams = [ftsQuery];
					if (stage) { countQuery += ' AND b.stage = ?'; countParams.push(Number(stage)); }
					if (status) { countQuery += ' AND b.current_status = ?'; countParams.push(status); }
					if (updatedAfter) { countQuery += ' AND b.status_changed_at >= ?'; countParams.push(updatedAfter); }
					if (updatedBefore) { countQuery += ' AND b.status_changed_at <= ?'; countParams.push(updatedBefore + ' 23:59:59'); }
					if (analyzed === '1') { countQuery += ' AND EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)'; }
					if (analyzed === '0') { countQuery += ' AND NOT EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)'; }
					const countResult = await env.radacleaner_db.prepare(countQuery).bind(...countParams).first();

					return json({ bills: results, limit, offset, total: countResult?.total || 0, search_engine: 'fts5' });
				}

				// Non-search query: use LIKE (original path)
				let query = `SELECT b.id, b.bill_number, b.title, b.current_status, b.registration_date, b.committee, b.stage, b.updated_at, b.status_changed_at, ra.has_analysis,
					CASE WHEN ra.json_data LIKE '%"risk_level": "high"%' THEN 'high'
					     WHEN ra.json_data LIKE '%"risk_level": "medium"%' THEN 'medium'
					     WHEN ra.json_data LIKE '%"risk_level": "low"%' THEN 'low'
					     ELSE NULL END as risk_level
					FROM bills b LEFT JOIN (SELECT bill_id, 1 as has_analysis, json_data FROM risk_assessments) ra ON ra.bill_id = b.id WHERE 1=1`;

				if (stage) { query += ' AND b.stage = ?'; params.push(Number(stage)); }
				if (status) { query += ' AND b.current_status = ?'; params.push(status); }
				if (updatedAfter) { query += ' AND b.status_changed_at >= ?'; params.push(updatedAfter); }
				if (updatedBefore) { query += ' AND b.status_changed_at <= ?'; params.push(updatedBefore + ' 23:59:59'); }
				if (analyzed === '1') { query += ' AND EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)'; }
				if (analyzed === '0') { query += ' AND NOT EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)'; }

				query += ` ORDER BY b.${safeSort} ${order} LIMIT ? OFFSET ?`;
				params.push(limit, offset);

				const { results } = await env.radacleaner_db.prepare(query).bind(...params).all();

				// COUNT using COUNT(*) OVER() window function — single query instead of two
				let countQuery = 'SELECT COUNT(*) OVER() as total FROM bills b WHERE 1=1';
				const countParams = [];
				if (stage) { countQuery += ' AND b.stage = ?'; countParams.push(Number(stage)); }
				if (status) { countQuery += ' AND b.current_status = ?'; countParams.push(status); }
				if (updatedAfter) { countQuery += ' AND b.status_changed_at >= ?'; countParams.push(updatedAfter); }
				if (updatedBefore) { countQuery += ' AND b.status_changed_at <= ?'; countParams.push(updatedBefore + ' 23:59:59'); }
				if (analyzed === '1') { countQuery += ' AND EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)'; }
				if (analyzed === '0') { countQuery += ' AND NOT EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)'; }
				countQuery += ` ORDER BY b.${safeSort} ${order} LIMIT 1`;
				const countResult = await env.radacleaner_db.prepare(countQuery).bind(...countParams).first();

				return json({ bills: results, limit, offset, total: countResult?.total || 0 });
			}

			// --- SINGLE BILL ---
			const billMatch = pathname.match(/^\/api\/bills\/(\d+)$/);
			if (method === 'GET' && billMatch) {
				const id = Number(billMatch[1]);
				const bill = await env.radacleaner_db.prepare(
					'SELECT id, bill_number, title, current_status, registration_date, committee, agenda_category, url, stage, act_number, act_date, created_at, updated_at, status_changed_at FROM bills WHERE id = ?'
				).bind(id).first();
				if (!bill) return error('Bill not found', 404);

				const risks = await env.radacleaner_db.prepare(
					'SELECT bill_id, model_used, overall_score, budget_risk, legal_risk, economic_risk, social_risk, corruption_risk, legislative_risk, official_power_risk, vague_norms_risk, confidence_level, insufficient_text, raw_analysis, json_data, raw_response FROM risk_assessments WHERE bill_id = ?'
				).bind(id).first();
				const { results: versions } = await env.radacleaner_db.prepare(
					'SELECT id, version_date, status_at_moment, text_hash FROM law_versions WHERE law_id = ? ORDER BY version_date DESC LIMIT 10'
				).bind(id).all();
				const { results: changes } = await env.radacleaner_db.prepare(
					'SELECT id, change_type, old_value, new_value, created_at FROM change_log WHERE bill_id = ? ORDER BY created_at DESC LIMIT 20'
				).bind(id).all();
				const { results: documents } = await env.radacleaner_db.prepare(
					'SELECT id, bill_id, file_id, doc_type FROM bill_documents WHERE bill_id = ? ORDER BY doc_type'
				).bind(id).all();

				const { results: passings } = await env.radacleaner_db.prepare(
					'SELECT pass_date, title, status FROM bill_passings WHERE bill_id = ? ORDER BY pass_date DESC'
				).bind(id).all();

				// Fetch votes with counts and deputy-level details
				const { results: votes } = await env.radacleaner_db.prepare(`
					SELECT v.vote_id, v.bill_id, v.vote_date, v.title,
						SUM(CASE WHEN vs.code='yes' THEN 1 ELSE 0 END) as yes_count,
						SUM(CASE WHEN vs.code='no' THEN 1 ELSE 0 END) as no_count,
						SUM(CASE WHEN vs.code='abstain' THEN 1 ELSE 0 END) as abstain_count,
						SUM(CASE WHEN vs.code='not_present' THEN 1 ELSE 0 END) as not_present_count,
						SUM(CASE WHEN vs.code='absent' THEN 1 ELSE 0 END) as absent_count
					FROM votes v
					LEFT JOIN mp_votes mv ON mv.vote_id = v.vote_id
					LEFT JOIN vote_statuses vs ON mv.status_id = vs.id
					WHERE v.bill_id = ?
					GROUP BY v.vote_id
					ORDER BY v.vote_date ASC
				`).bind(id).all();

				for (const vote of votes) {
					const { results: deputies } = await env.radacleaner_db.prepare(
						'SELECT mv.mp_name, mv.mp_faction, vs.code as vote_code, vs.label as vote_label FROM mp_votes mv JOIN vote_statuses vs ON mv.status_id = vs.id WHERE mv.vote_id = ? ORDER BY mv.mp_faction, mv.mp_name'
					).bind(vote.vote_id).all();
					vote.deputies = deputies;
				}

				return json({ bill, risks, versions, changes, votes, documents, passings });
			}

			// --- BILL VERSIONS (for diff) ---
			const billVersionsMatch = pathname.match(/^\/api\/bills\/(\d+)\/versions$/);
			if (method === 'GET' && billVersionsMatch) {
				const billId = Number(billVersionsMatch[1]);
				const { results } = await env.radacleaner_db.prepare(
					'SELECT id, law_id, version_date, status_at_moment, text_hash, plain_text, analysis_summary, risks_json FROM law_versions WHERE law_id = ? ORDER BY version_date DESC LIMIT 10'
				).bind(billId).all();
				return json({ versions: results });
			}

			// --- BILL RISKS ---
			const billRisksMatch = pathname.match(/^\/api\/bills\/(\d+)\/risks$/);
			if (method === 'GET' && billRisksMatch) {
				const risks = await env.radacleaner_db.prepare(
					'SELECT bill_id, model_used, overall_score, budget_risk, legal_risk, economic_risk, social_risk, corruption_risk, legislative_risk, official_power_risk, vague_norms_risk, confidence_level, insufficient_text FROM risk_assessments WHERE bill_id = ?'
				).bind(Number(billRisksMatch[1])).first();
				if (!risks) return error('No risks found', 404);
				return json({ risks });
			}

			// --- BILL VOTES ---
			const billVotesMatch = pathname.match(/^\/api\/bills\/(\d+)\/votes$/);
			if (method === 'GET' && billVotesMatch) {
				const { results } = await env.radacleaner_db.prepare(
					'SELECT vote_id, bill_id, vote_date, title FROM votes WHERE bill_id = ? ORDER BY vote_date DESC'
				).bind(Number(billVotesMatch[1])).all();
				return json({ votes: results });
			}

			// --- VOTES ---
			if (method === 'GET' && pathname === '/api/votes') {
				const limit = Math.min(Number(url.searchParams.get('limit')) || 20, 100);
				const billId = url.searchParams.get('bill_id');

				let query = 'SELECT v.vote_id, v.bill_id, v.vote_date, v.title, b.bill_number, b.title as bill_title FROM votes v LEFT JOIN bills b ON v.bill_id = b.id';
				const params = [];

				if (billId) { query += ' WHERE v.bill_id = ?'; params.push(Number(billId)); }
				query += ' ORDER BY v.vote_date DESC LIMIT ?';
				params.push(limit);

				const { results } = await env.radacleaner_db.prepare(query).bind(...params).all();

				for (const vote of results) {
					const { results: factions } = await env.radacleaner_db.prepare(`
						SELECT mp_faction, COUNT(*) as total,
							SUM(CASE WHEN vs.code='yes' THEN 1 ELSE 0 END) as yes,
							SUM(CASE WHEN vs.code='no' THEN 1 ELSE 0 END) as no,
							SUM(CASE WHEN vs.code='abstain' THEN 1 ELSE 0 END) as abstain
						FROM mp_votes mv JOIN vote_statuses vs ON mv.status_id=vs.id
						WHERE mv.vote_id=? GROUP BY mp_faction ORDER BY total DESC
					`).bind(vote.vote_id).all();
					vote.factions = factions;
				}
				return json({ votes: results });
			}

			// --- DEPUTY ---
			const deputyMatch = pathname.match(/^\/api\/deputies\/(.+)$/);
			if (method === 'GET' && deputyMatch) {
				const param = decodeURIComponent(deputyMatch[1]);
				const isNum = /^\d+$/.test(param);
				const deputy = isNum
					? await env.radacleaner_db.prepare('SELECT id, name, faction, start_date FROM mps WHERE id = ?').bind(Number(param)).first()
					: await env.radacleaner_db.prepare('SELECT id, name, faction, start_date FROM mps WHERE name = ?').bind(param).first();
				if (!deputy) return error('Deputy not found', 404);

				const { results: votes } = await env.radacleaner_db.prepare(`
					SELECT mv.mp_name, mv.mp_faction, vs.code as vote_code, vs.label as vote_label,
						v.title as vote_title, v.vote_date, b.bill_number
					FROM mp_votes mv
					JOIN vote_statuses vs ON mv.status_id=vs.id
					JOIN votes v ON mv.vote_id=v.vote_id
					LEFT JOIN bills b ON v.bill_id=b.id
					WHERE mv.mp_name=?
					ORDER BY v.vote_date DESC LIMIT 50
				`).bind(deputy.name).all();
				
				const { results: voteStats } = await env.radacleaner_db.prepare(`
					SELECT COUNT(*) as total,
						SUM(CASE WHEN vs.code IN ('yes','no','abstain') THEN 1 ELSE 0 END) as attended,
						SUM(CASE WHEN vs.code IN ('yes','no') THEN 1 ELSE 0 END) as voted
					FROM mp_votes mv
					JOIN vote_statuses vs ON mv.status_id = vs.id
					JOIN votes v ON mv.vote_id = v.vote_id
					WHERE mv.mp_name = ?
					AND (? IS NULL OR v.vote_date >= ?)
				`).bind(deputy.name, deputy.start_date || null, deputy.start_date || null).all();
				
				const total = voteStats[0]?.total || 0;
				const attended = voteStats[0]?.attended || 0;
				const voted = voteStats[0]?.voted || 0;
				
				const MIN_VOTES = 5;
				const dataSufficient = total >= MIN_VOTES;
				const py = total > 0 ? Math.round((attended / total) * 100) : 0;
				const pda = attended > 0 ? Math.round((voted / attended) * 100) : 0;
				
				const { results: weightedVotes } = await env.radacleaner_db.prepare(`
					SELECT v.title,
						CASE 
							WHEN v.title LIKE '%процедурн%' THEN 1
							WHEN v.title LIKE '%друге читання%' THEN 2
							WHEN v.title LIKE '%прийняття%' OR v.title LIKE '%останнє%' THEN 3
							ELSE 1
						END as weight,
						CASE 
							WHEN vs.code IN ('yes','no') THEN 1.0
							WHEN vs.code = 'abstain' THEN 0.5
							ELSE 0.0
						END as action
					FROM mp_votes mv
					JOIN vote_statuses vs ON mv.status_id = vs.id
					JOIN votes v ON mv.vote_id = v.vote_id
					WHERE mv.mp_name = ?
					AND (? IS NULL OR v.vote_date >= ?)
				`).bind(deputy.name, deputy.start_date || null, deputy.start_date || null).all();
				
				let weightedSum = 0;
				let weightTotal = 0;
				for (const wv of weightedVotes) {
					weightedSum += wv.weight * wv.action;
					weightTotal += wv.weight;
				}
				const vkp = weightTotal > 0 ? Math.round((weightedSum / weightTotal) * 100) : 0;
				
				return json({ deputy, votes, stats: { total, attended, py, pda, vkp, dataSufficient } });
			}

			// --- DEPUTIES LIST ---
			if (method === 'GET' && pathname === '/api/deputies') {
				const limit = Math.min(Number(url.searchParams.get('limit')) || 100, 500);
				const offset = Number(url.searchParams.get('offset')) || 0;
				const search = url.searchParams.get('search');
				const faction = url.searchParams.get('faction');
				const sort = url.searchParams.get('sort') || 'name';
				const order = (url.searchParams.get('order') || 'ASC').toUpperCase() === 'ASC' ? 'ASC' : 'DESC';

				let whereClause = 'WHERE 1=1';
				const params = [];

				if (search) { whereClause += ' AND m.name LIKE ?'; params.push(`%${search}%`); }
				if (faction) { whereClause += ' AND m.faction = ?'; params.push(faction); }

				const safeSort = ['name','faction'].includes(sort) ? sort : 'name';

				const dataQuery = `
					SELECT 
						m.id, m.name, m.faction, m.start_date,
						COALESCE(m.py, 0) as py,
						COALESCE(m.pda, 0) as pda,
						COALESCE(m.vkp, 0) as vkp,
						COALESCE(m.data_sufficient, 0) as dataSufficient,
						COALESCE(m.total_votes, 0) as total,
						COALESCE(m.attended_votes, 0) as attended,
						COALESCE(m.voted_votes, 0) as voted,
						COALESCE(m.total_bills, 0) as totalBills,
						COALESCE(m.total_laws, 0) as totalLaws
					FROM mps m
					${whereClause}
					ORDER BY m.${safeSort} ${order}
					LIMIT ? OFFSET ?
				`;
				const dataParams = [...params, limit, offset];

				const countQuery = `SELECT COUNT(*) as total FROM mps m ${whereClause}`;

				const [{ results }, countResult] = await Promise.all([
					env.radacleaner_db.prepare(dataQuery).bind(...dataParams).all(),
					env.radacleaner_db.prepare(countQuery).bind(...params).first()
				]);

				const deputiesWithStats = results.map(d => {
					return {
						...d,
						py: d.py || 0,
						pda: d.pda || 0,
						vkp: d.vkp || 0,
						dataSufficient: d.dataSufficient || false,
						total: d.total || 0,
						attended: d.attended || 0,
						voted: d.voted || 0,
						totalBills: d.totalBills || 0,
						totalLaws: d.totalLaws || 0,
						conversion: d.totalBills > 0 ? Math.round((d.totalLaws / d.totalBills) * 100) : 0,
					};
				});

				return json({ deputies: deputiesWithStats, total: countResult?.total || 0 });
			}

			// --- FACTIONS LIST ---
			if (method === 'GET' && pathname === '/api/factions') {
				const { results } = await env.radacleaner_db.prepare(
					'SELECT DISTINCT faction FROM mps WHERE faction IS NOT NULL AND faction != "" ORDER BY faction'
				).all();
				return json({ factions: results.map(r => r.faction) }, 200, 300);
			}

			// --- PLENARY SESSIONS (calendar) ---
			if (method === 'GET' && pathname === '/api/plenary-sessions') {
				const { results } = await env.radacleaner_db.prepare(`
					SELECT DISTINCT DATE(v.vote_date) as session_date
					FROM votes v
					WHERE v.bill_id IS NOT NULL
					ORDER BY v.vote_date DESC
					LIMIT 100
				`).raw().all();

				// For each date, get bills voted on
				const sessions = await Promise.all((results || []).map(async (r) => {
					const { results: bills } = await env.radacleaner_db.prepare(`
						SELECT b.bill_number, b.title
						FROM votes v
						JOIN bills b ON v.bill_id = b.id
						WHERE DATE(v.vote_date) = DATE(?)
						LIMIT 20
					`).bind(r.session_date).all();
					return { date: r.session_date, bills };
				}));

				return json({ sessions });
			}

			// --- GET /api/query (для Python-скриптів) ---
			if (method === 'GET' && pathname === '/api/query') {
				const auth = request.headers.get('Authorization') || '';
				const token = auth.replace('Bearer ', '');
				if (!token || token !== env.SYNC_TOKEN) return error('Unauthorized', 401);

				const sql = url.searchParams.get('sql');
				if (!sql) return error('Missing sql parameter', 400);

				// Безпекова перевірка: тільки SELECT
				const trimmed = sql.trim().toUpperCase();
				if (!trimmed.startsWith('SELECT') && !trimmed.startsWith('PRAGMA')) {
					return error('Only SELECT queries allowed', 403);
				}

				const params = [];
				let idx = 0;
				while (true) {
					const p = url.searchParams.get(`p${idx}`);
					if (p === null) break;
					params.push(p);
					idx++;
				}

				let result;
				if (params.length > 0) {
					result = await env.radacleaner_db.prepare(sql).bind(...params).all();
				} else {
					result = await env.radacleaner_db.prepare(sql).all();
				}
				return json({ results: result.results });
			}

			// --- POST /api/query (для складних запитів з JSON body) ---
			if (method === 'POST' && pathname === '/api/query') {
				const auth = request.headers.get('Authorization') || '';
				const token = auth.replace('Bearer ', '');
				if (!token || token !== env.SYNC_TOKEN) return error('Unauthorized', 401);

				const body = await request.json();
				const { sql, params } = body;
				if (!sql) return error('Missing sql', 400);

				const trimmed = sql.trim().toUpperCase();
				if (!trimmed.startsWith('SELECT') && !trimmed.startsWith('PRAGMA')) {
					return error('Only SELECT queries allowed', 403);
				}

				let result;
				if (params && params.length > 0) {
					result = await env.radacleaner_db.prepare(sql).bind(...params).all();
				} else {
					result = await env.radacleaner_db.prepare(sql).all();
				}
				return json({ results: result.results });
			}

			// --- POST /api/sync ---
			if (method === 'POST' && pathname === '/api/sync') {
				const auth = request.headers.get('Authorization') || '';
				const token = auth.replace('Bearer ', '');
				if (!token || token !== env.SYNC_TOKEN) return error('Unauthorized', 401);

				const body = await request.json();
				const { type, data } = body;

				// Допоміжна функція: знайти D1 bill_id за bill_number
				async function resolveBillId(billNumber) {
					if (!billNumber) return null;
					const bill = await env.radacleaner_db.prepare(
						'SELECT id FROM bills WHERE bill_number = ?'
					).bind(String(billNumber)).first();
					return bill ? bill.id : null;
				}

				// Універсальний raw SQL (INSERT/UPDATE/DELETE)
				if (type === 'raw_sql') {
					if (!data.sql) return error('raw_sql requires sql', 400);
					const result = await env.radacleaner_db.prepare(data.sql)
						.bind(...(data.params || [])).run();
					return json({ success: true, meta: result.meta });
				}

				switch (type) {
					case 'bill':
						await env.radacleaner_db.prepare(`
							INSERT INTO bills (bill_number, title, current_status, registration_date, committee, agenda_category, url, stage, act_number, act_date)
							VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
							ON CONFLICT(bill_number) DO UPDATE SET
								current_status=COALESCE(?,current_status),
								title=COALESCE(?,title),
								registration_date=COALESCE(?,registration_date),
								committee=COALESCE(?,committee),
								agenda_category=COALESCE(?,agenda_category),
								url=COALESCE(?,url),
								stage=COALESCE(?,stage),
								act_number=COALESCE(?,act_number),
								act_date=COALESCE(?,act_date),
								updated_at=datetime('now')
						`).bind(
							data.bill_number||'', data.title||'', data.current_status||'new',
							data.registration_date||null, data.committee||'', data.agenda_category||'other',
							data.url||'', data.stage||1, data.act_number||null, data.act_date||null,
							data.current_status||null, data.title||null,
							data.registration_date||null, data.committee||null,
							data.agenda_category||null, data.url||null, data.stage||null,
							data.act_number||null, data.act_date||null,
						).run();
						return json({ success: true });

					case 'risk': {
						// Підтримка bill_number як альтернативи bill_id
						let billId = data.bill_id;
						if (!billId && data.bill_number) {
							billId = await resolveBillId(data.bill_number);
						}
						if (!billId) return error('risk requires bill_id or bill_number', 400);

						await env.radacleaner_db.prepare(`
							INSERT INTO risk_assessments (document_id, bill_id, model_used, overall_score, budget_risk, legal_risk,
								economic_risk, social_risk, corruption_risk, raw_response, raw_analysis, json_data,
								legislative_risk, official_power_risk, vague_norms_risk, confidence_level, insufficient_text)
							VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
							ON CONFLICT(bill_id) DO UPDATE SET assessed_at=datetime('now')
						`).bind(
							data.document_id||null, billId, data.model_used||'', data.overall_score||0,
							data.budget_risk||'{}', data.legal_risk||'{}', data.economic_risk||'{}',
							data.social_risk||'{}', data.corruption_risk||'{}',
							data.raw_response||'{}', data.raw_analysis||'', data.json_data||'{}',
							data.legislative_risk||'{}', data.official_power_risk||'{}', data.vague_norms_risk||'{}',
							data.confidence_level||5, data.insufficient_text?1:0,
						).run();
						return json({ success: true });
					}

					case 'change_log': {
						// Підтримка bill_number як альтернативи bill_id
						let billId = data.bill_id;
						if (!billId && data.bill_number) {
							billId = await resolveBillId(data.bill_number);
						}
						if (!billId) return error('change_log requires bill_id or bill_number', 400);

						await env.radacleaner_db.prepare(
							'INSERT INTO change_log (bill_id, change_type, old_value, new_value) VALUES (?,?,?,?)'
						).bind(billId, data.change_type, data.old_value||null, data.new_value||null).run();
						return json({ success: true });
					}

					case 'law_version': {
						// Підтримка bill_number як альтернативи law_id
						let lawId = data.law_id;
						if (!lawId && data.bill_number) {
							lawId = await resolveBillId(data.bill_number);
						}
						if (!lawId) return error('law_version requires law_id or bill_number', 400);

						await env.radacleaner_db.prepare(`
							INSERT INTO law_versions (law_id, status_at_moment, text_hash, plain_text, analysis_summary, risks_json)
							VALUES (?,?,?,?,?,?) ON CONFLICT(law_id, text_hash) DO NOTHING
						`).bind(lawId, data.status_at_moment||'', data.text_hash,
							data.plain_text||'', data.analysis_summary||'', data.risks_json||'{}').run();
						return json({ success: true });
					}

					default: return error(`Unknown type: ${type}`, 400);
				}
			}

			return error('Not found', 404);
		} catch (e) {
			return error(`Internal error: ${e?.message || String(e)}`, 500);
		}
	},
};

// Хелпер для D1 запитів з різними типами повернення
async function db(env, sql, mode) {
	const stmt = env.radacleaner_db.prepare(sql);
	if (mode === 'first') return await stmt.first();
	const { results } = await stmt.all();
	return results;
}