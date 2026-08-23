const express = require('express');
const cors = require('cors');
const { Pool } = require('pg');

const app = express();
app.use(cors());
app.use(express.json());

// Simple rate limiter: 100 req/min per IP
const rateLimit = {};
setInterval(() => { for (const k in rateLimit) delete rateLimit[k]; }, 60000);
app.use((req, res, next) => {
  const ip = req.ip;
  rateLimit[ip] = (rateLimit[ip] || 0) + 1;
  if (rateLimit[ip] > 100) return res.status(429).json({ error: 'Rate limit' });
  next();
});

const pool = new Pool({
  host: process.env.PG_HOST || '192.168.1.244',
  database: process.env.PG_DB || 'radacleaner',
  user: process.env.PG_USER || 'postgres',
  password: process.env.PG_PASS || '164352',
  max: 20,
  idleTimeoutMillis: 30000,
});

const API_KEY = process.env.API_KEY || '';

async function q(sql, params) {
  const { rows } = await pool.query(sql, params);
  return rows;
}

function json(res, data, status = 200, cacheSeconds) {
  const headers = { 'Content-Type': 'application/json' };
  if (cacheSeconds) headers['Cache-Control'] = `public, max-age=${cacheSeconds}`;
  res.status(status).set(headers).json(data);
}

function error(res, msg, status = 400) {
  json(res, { error: msg }, status);
}

// --- BILLS BY YEAR (for dashboard chart) ---
app.get('/api/bills-by-year', async (req, res) => {
  try {
    const results = await q(`
      SELECT 
        EXTRACT(YEAR FROM registration_date::date) as year,
        SUM(CASE WHEN stage = 4 THEN 1 ELSE 0 END) as signed,
        SUM(CASE WHEN stage IN (1,2,3) THEN 1 ELSE 0 END) as in_process,
        SUM(CASE WHEN stage = 5 THEN 1 ELSE 0 END) as rejected
      FROM bills 
      WHERE registration_date IS NOT NULL AND registration_date != ''
      GROUP BY year
      ORDER BY year
    `);
    json(res, { data: results }, 200, 3600);
  } catch (e) { error(res, e.message, 500); }
});

// --- STATS ---
app.get('/api/stats', async (req, res) => {
  try {
    const rows = await q('SELECT key, value FROM stats_cache');
    const cache = {};
    for (const r of rows) cache[r.key] = r.value;
    const byStage = JSON.parse(cache.by_stage || '[]');
    json(res, {
      totalBills: Number(cache.total_bills) || 0, byStage,
      highRiskBills: Number(cache.high_risk) || 0,
      mediumRiskBills: Number(cache.medium_risk) || 0,
      recentChanges: Number(cache.recent_changes) || 0,
      totalVotes: Number(cache.total_votes) || 0,
      totalMps: Number(cache.total_mps) || 0,
      activeMps: Number(cache.active_mps) || 0,
      lastSync: cache.last_updated || null,
      analyzedBills: Number(cache.analyzed_bills) || 0,
      proceduralBills: Number(cache.procedural_bills) || 0,
      newBills24h: Number(cache.new_bills_24h) || 0,
      statusChanges24h: Number(cache.status_changes_24h) || 0,
      avgToxicity: Number(cache.avg_toxicity) || 0,
    }, 200, 30);
  } catch (e) { error(res, e.message, 500); }
});

// --- STATUSES ---
app.get('/api/statuses', async (req, res) => {
  try {
    const results = await q('SELECT current_status, COUNT(*) as count FROM bills WHERE current_status IS NOT NULL AND current_status != \'\' GROUP BY current_status ORDER BY count DESC');
    json(res, { statuses: results }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

// --- BY STAGE ---
app.get('/api/by-stage', async (req, res) => {
  try {
    const results = await q('SELECT stage, current_status, COUNT(*) as count FROM bills GROUP BY stage, current_status ORDER BY stage, count DESC');
    json(res, { data: results }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

// --- BILLS LIST ---
app.get('/api/bills', async (req, res) => {
  try {
    const limit = Math.min(Number(req.query.limit) || 50, 200);
    const offset = Number(req.query.offset) || 0;
    const stage = req.query.stage;
    const status = req.query.status;
    const search = req.query.search;
    const sort = req.query.sort || 'status_changed_at';
    const order = (req.query.order || 'DESC').toUpperCase() === 'ASC' ? 'ASC' : 'DESC';
    const updatedAfter = req.query.updated_after;
    const updatedBefore = req.query.updated_before;
    const analyzed = req.query.analyzed;
    const threats = req.query.threats;
    const procedural = req.query.procedural || '';

    const PROCEDURAL_CATEGORIES = ['Організаційні питання', 'Інші (заяви, звернення ВРУ)'];
    const safeSort = ['created_at','updated_at','status_changed_at','registration_date','bill_number','stage','current_status','act_date','toxicity','significance'].includes(sort) ? sort : 'status_changed_at';

    let where = ['1=1'];
    let params = [];
    let idx = 1;

    if (stage) {
      const stages = stage.split(',').map(s => Number(s.trim())).filter(s => s > 0);
      if (stages.length === 1) {
        where.push(`b.stage = $${idx++}`);
        params.push(stages[0]);
      } else if (stages.length > 1) {
        const placeholders = stages.map(() => `$${idx++}`).join(',');
        where.push(`b.stage IN (${placeholders})`);
        params.push(...stages);
      }
    }
    if (status) { where.push(`b.current_status = $${idx++}`); params.push(status); }
    if (updatedAfter) { where.push(`b.status_changed_at >= $${idx++}`); params.push(updatedAfter); }
    if (updatedBefore) { where.push(`b.status_changed_at <= $${idx++}`); params.push(updatedBefore + ' 23:59:59'); }
    if (analyzed === '1') { where.push(`EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)`); }
    if (analyzed === '0') { where.push(`NOT EXISTS (SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id)`); }

    if (procedural === 'only') {
      const catPH = PROCEDURAL_CATEGORIES.map(() => `$${idx++}`).join(',');
      where.push(`(b.is_procedural = 1 OR (b.is_procedural IS NULL AND b.agenda_category IN (${catPH})))`);
      params.push(...PROCEDURAL_CATEGORIES);
    } else if (procedural !== '1') {
      where.push(`(b.is_procedural = 0 OR b.is_procedural IS NULL)`);
    }

    if (threats === '1') {
      where.push(`EXISTS (SELECT 1 FROM risk_assessments ra2 WHERE ra2.bill_id = b.id AND (ra2.risk_level = 'high' OR ra2.overall_score >= 70))`);
    } else if (threats === '2') {
      where.push(`EXISTS (SELECT 1 FROM risk_assessments ra2 WHERE ra2.bill_id = b.id AND (ra2.risk_level IN ('high','medium') OR ra2.overall_score >= 40))`);
    }

    const whereSQL = where.join(' AND ');

    let query, countQuery, countParams;

    if (search && search.trim()) {
      const searchPattern = `%${search.trim()}%`;
      params.push(searchPattern);
      query = `SELECT b.id, b.bill_number, b.title, b.current_status, b.registration_date, b.committee, b.stage, b.updated_at, b.status_changed_at, b.agenda_category, b.is_procedural, b.significance, b.impact, b.risk_score, b.toxicity, ra.has_analysis, ra.risk_level
        FROM bills b
        LEFT JOIN (SELECT bill_id, 1 as has_analysis, risk_level FROM risk_assessments) ra ON ra.bill_id = b.id
        WHERE (b.bill_number ILIKE $${idx} OR b.title ILIKE $${idx}) AND ${whereSQL}
        ORDER BY b.${safeSort} ${order} LIMIT $${idx+1} OFFSET $${idx+2}`;
      params.push(limit, offset);
      countQuery = `SELECT COUNT(*) as total FROM bills b WHERE (b.bill_number ILIKE $${idx} OR b.title ILIKE $${idx}) AND ${whereSQL}`;
      countParams = params.slice(0, -2);
    }

    if (!query) {
      query = `SELECT b.id, b.bill_number, b.title, b.current_status, b.registration_date, b.committee, b.stage, b.updated_at, b.status_changed_at, b.agenda_category, b.is_procedural, b.significance, b.impact, b.risk_score, b.toxicity, ra.has_analysis, ra.risk_level
        FROM bills b LEFT JOIN (SELECT bill_id, 1 as has_analysis, risk_level FROM risk_assessments) ra ON ra.bill_id = b.id
        WHERE ${whereSQL}
        ORDER BY b.${safeSort} ${order} LIMIT $${idx++} OFFSET $${idx++}`;
      params.push(limit, offset);
      countQuery = `SELECT COUNT(*) as total FROM bills b WHERE ${whereSQL}`;
      countParams = params.slice(0, -2);
    }

    const bills = await q(query, params);
    const countResult = await q(countQuery, countParams);
    json(res, { bills, limit, offset, total: Number(countResult[0]?.total) || 0 }, 200, 60);
  } catch (e) { error(res, e.message, 500); }
});

// --- SINGLE BILL ---
app.get('/api/bills/:id', async (req, res) => {
  try {
    const id = Number(req.params.id);
    const bill = (await q('SELECT id, bill_number, title, current_status, registration_date, committee, agenda_category, url, stage, act_number, act_date, created_at, updated_at, status_changed_at, significance, impact, risk_score, toxicity FROM bills WHERE id = $1', [id]))[0];
    if (!bill) return error(res, 'Bill not found', 404);

    const risks = (await q('SELECT * FROM risk_assessments WHERE bill_id = $1', [id]))[0];
    const versions = await q('SELECT id, version_date, status_at_moment, text_hash, plain_text FROM law_versions WHERE law_id = $1 ORDER BY version_date DESC LIMIT 10', [id]);
    const changes = await q('SELECT id, change_type, old_value, new_value, created_at FROM change_log WHERE bill_id = $1 ORDER BY created_at DESC LIMIT 20', [id]);
    const documents = await q('SELECT id, bill_id, file_id, doc_type FROM bill_documents WHERE bill_id = $1 ORDER BY doc_type', [id]);
    const passings = await q('SELECT pass_date, title, status FROM bill_passings WHERE bill_id = $1 ORDER BY pass_date DESC', [id]);

    const votesRaw = await q(`SELECT vote_id, bill_id, vote_date, title,
      yes_count, no_count, abstain_count, not_present_count, absent_count
    FROM votes WHERE bill_id = $1 ORDER BY vote_date ASC`, [id]);

    for (const vote of votesRaw) {
      vote.deputies = await q('SELECT COALESCE(m.name, mv.mp_name) as mp_name, COALESCE(m.faction, mv.mp_faction) as mp_faction, vs.code as vote_code, vs.label as vote_label FROM mp_votes mv JOIN vote_statuses vs ON mv.status_id = vs.id LEFT JOIN mps m ON m.id = mv.mp_id WHERE mv.vote_id = $1 ORDER BY mp_faction, mp_name', [vote.vote_id]);
    }

    json(res, { bill, risks, versions, changes, votes: votesRaw, documents, passings });
  } catch (e) { error(res, e.message, 500); }
});

// --- BILL VERSIONS ---
app.get('/api/bills/:id/versions', async (req, res) => {
  try {
    const results = await q('SELECT id, law_id, version_date, status_at_moment, text_hash, plain_text, analysis_summary, risks_json FROM law_versions WHERE law_id = $1 ORDER BY version_date DESC LIMIT 10', [Number(req.params.id)]);
    json(res, { versions: results });
  } catch (e) { error(res, e.message, 500); }
});

// --- BILL RISKS ---
app.get('/api/bills/:id/risks', async (req, res) => {
  try {
    const risks = (await q('SELECT * FROM risk_assessments WHERE bill_id = $1', [Number(req.params.id)]))[0];
    if (!risks) return error(res, 'No risks found', 404);
    json(res, { risks });
  } catch (e) { error(res, e.message, 500); }
});

// --- BILL ANALYZE ---
app.post('/api/bills/:id/analyze', async (req, res) => {
  try {
    const id = Number(req.params.id);
    const bill = (await q('SELECT id, bill_number, title, current_status, url FROM bills WHERE id = $1', [id]))[0];
    if (!bill) return error(res, 'Bill not found', 404);
    await q('INSERT INTO pending_analysis (bill_id, bill_number, status) VALUES ($1, $2, $3)', [id, bill.bill_number, 'running']);

    // Run analysis in background, return immediately
    const { execFile } = require('child_process');
    const pythonPath = process.env.PYTHON_PATH || '/home/radamon/radacleaner/venv/bin/python';
    const scriptPath = '/home/radamon/radacleaner/analyze_bill.py';

    execFile(pythonPath, [scriptPath, bill.bill_number, '--force'], {
      timeout: 300000,
      env: { ...process.env, PYTHONPATH: '/home/radamon/radacleaner' },
    }, async (err, stdout, stderr) => {
      const status = err ? 'error' : 'done';
      const output = (stdout || '') + (stderr || '');
      try {
        await q('UPDATE pending_analysis SET status=$1, output=$2, finished_at=now() WHERE bill_id=$3 AND status=\'running\'',
          [status, output.slice(-2000), id]);
      } catch (e) {}
      if (status === 'done') {
        try { await q('REFRESH MATERIALIZED VIEW IF EXISTS stats_cache'); } catch(e) {}
      }
    });

    json(res, { status: 'triggered', bill_number: bill.bill_number });
  } catch (e) { error(res, e.message, 500); }
});

app.get('/api/bills/:id/analyze', async (req, res) => {
  try {
    const id = Number(req.params.id);
    const pending = (await q('SELECT * FROM pending_analysis WHERE bill_id = $1 ORDER BY id DESC LIMIT 1', [id]))[0];
    if (!pending) return json(res, { status: 'none' });
    json(res, { status: pending.status, output: pending.output || '', created: pending.created_at, finished: pending.finished_at });
  } catch (e) { error(res, e.message, 500); }
});

// --- BILL VOTES ---
app.get('/api/bills/:id/votes', async (req, res) => {
  try {
    const results = await q('SELECT vote_id, bill_id, vote_date, title FROM votes WHERE bill_id = $1 ORDER BY vote_date DESC', [Number(req.params.id)]);
    json(res, { votes: results });
  } catch (e) { error(res, e.message, 500); }
});

// --- VOTES ---
app.get('/api/votes', async (req, res) => {
  try {
    const limit = Math.min(Number(req.query.limit) || 20, 100);
    const billId = req.query.bill_id;
    let query = 'SELECT v.vote_id, v.bill_id, v.vote_date, v.title, b.bill_number, b.title as bill_title FROM votes v LEFT JOIN bills b ON v.bill_id = b.id';
    const params = [];
    if (billId) { query += ' WHERE v.bill_id = $1'; params.push(Number(billId)); }
    query += ` ORDER BY v.vote_date DESC LIMIT $${params.length + 1}`;
    params.push(limit);
    const results = await q(query, params);
    for (const vote of results) {
      vote.factions = await q(`SELECT mp_faction, COUNT(*) as total, SUM(CASE WHEN vs.code='yes' THEN 1 ELSE 0 END) as yes, SUM(CASE WHEN vs.code='no' THEN 1 ELSE 0 END) as no, SUM(CASE WHEN vs.code='abstain' THEN 1 ELSE 0 END) as abstain FROM mp_votes mv JOIN vote_statuses vs ON mv.status_id=vs.id WHERE mv.vote_id=$1 GROUP BY mp_faction ORDER BY total DESC`, [vote.vote_id]);
    }
    json(res, { votes: results });
  } catch (e) { error(res, e.message, 500); }
});

// --- DEPUTY ---
app.get('/api/deputies/:name', async (req, res) => {
  try {
    const param = decodeURIComponent(req.params.name);
    const isNum = /^\d+$/.test(param);
    const deputy = isNum
      ? (await q('SELECT * FROM mps WHERE id = $1', [Number(param)]))[0]
      : (await q('SELECT * FROM mps WHERE name = $1', [param]))[0];
    if (!deputy) return error(res, 'Deputy not found', 404);

    const limit = Math.min(Number(req.query.limit) || 50, 200);
    const offset = Number(req.query.offset) || 0;

    const votes = (await q(`SELECT mv.mp_name, mv.mp_faction, vs.code as vote_code, vs.label as vote_label, v.title as vote_title, mv.vote_date, b.bill_number FROM mp_votes mv JOIN vote_statuses vs ON mv.status_id=vs.id JOIN votes v ON mv.vote_id=v.vote_id LEFT JOIN bills b ON v.bill_id=b.id WHERE mv.mp_name=$1 ORDER BY mv.vote_date DESC LIMIT $2 OFFSET $3`, [deputy.name, limit, offset]));
    const countResult = (await q('SELECT COUNT(*) as total FROM mp_votes mv WHERE mv.mp_name=$1', [deputy.name]))[0];
    const total = deputy.total_votes || 0;

    json(res, { deputy, votes, votesTotal: Number(countResult.total), votesLimit: limit, votesOffset: offset, stats: { total, attended: total, py: deputy.py || 0, pda: deputy.pda || 0, vkp: deputy.vkp || 0, dataSufficient: deputy.data_sufficient || false, lei: deputy.lei || 0, avgS: deputy.avg_s || 0, avgI: deputy.avg_i || 0, avgTox: deputy.avg_tox || 0, kpiScore: deputy.kpi_score || 0, kpiRank: deputy.kpi_rank || 0 } });
  } catch (e) { error(res, e.message, 500); }
});

// --- DEPUTIES LIST ---
app.get('/api/deputies', async (req, res) => {
  try {
    const limit = Math.min(Number(req.query.limit) || 100, 500);
    const offset = Number(req.query.offset) || 0;
    const search = req.query.search;
    const faction = req.query.faction;
    const status = req.query.status;
    const sort = req.query.sort || 'name';
    const order = (req.query.order || 'DESC').toUpperCase() === 'ASC' ? 'ASC' : 'DESC';
    const safeSort = ['name','faction','py','pda','vkp','conversion','lei','avg_s','avg_i','avg_tox','kpi_score','eu_integration_score','kpi_v11_score','kpi_v11_effectiveness','kpi_v11_discipline','kpi_v11_efficiency','kpi_v11_control','kpi_v11_quality','kpi_v12_score','kpi_v12_discipline','kpi_v12_legislation','kpi_v12_efficiency','kpi_v12_committee','kpi_v12_requests','kpi_v12_impact'].includes(sort) ? sort : 'name';
    const sortCol = safeSort === 'conversion'
      ? `CASE WHEN m.total_bills > 0 THEN m.total_laws::float / m.total_bills ELSE 0 END`
      : `m.${safeSort}`;

    let where = ['1=1'];
    let params = [];
    let idx = 1;
    if (search) { where.push(`m.name ILIKE $${idx++}`); params.push(`%${search}%`); }
    if (faction) { where.push(`m.faction = $${idx++}`); params.push(faction); }
    if (status === 'active') { where.push(`m.end_date IS NULL OR m.end_date = ''`); }
    else if (status === 'former') { where.push(`m.end_date IS NOT NULL AND m.end_date != ''`); }

    const whereSQL = where.join(' AND ');
    const deputies = await q(`SELECT m.id, m.name, m.faction, m.start_date, m.end_date, COALESCE(m.py,0) as py, COALESCE(m.pda,0) as pda, COALESCE(m.vkp,0) as vkp, COALESCE(m.data_sufficient,0) as "dataSufficient", COALESCE(m.total_votes,0) as total, COALESCE(m.attended_votes,0) as attended, COALESCE(m.voted_votes,0) as voted, COALESCE(m.total_bills,0) as "totalBills", COALESCE(m.total_laws,0) as "totalLaws", COALESCE(m.lei,0) as lei, COALESCE(m.avg_s,0) as "avgS", COALESCE(m.avg_i,0) as "avgI", COALESCE(m.avg_tox,0) as "avgTox", COALESCE(m.kpi_score,0) as "kpiScore", COALESCE(m.kpi_rank,0) as "kpiRank", COALESCE(m.eu_integration_score,0) as "euScore", COALESCE(m.eu_euro_bills,0) as "euEuroBills", COALESCE(m.eu_risk_bills,0) as "euRiskBills", COALESCE(m.eu_state_aid_bills,0) as "euStateAidBills", COALESCE(m.requests_with_response,0) as "requestsWithResponse", COALESCE(m.kpi_v11_score,0) as "kpiV11", COALESCE(m.kpi_v11_effectiveness,0) as "kpiEff", COALESCE(m.kpi_v11_discipline,0) as "kpiDisc", COALESCE(m.kpi_v11_efficiency,0) as "kpiRes", COALESCE(m.kpi_v11_control,0) as "kpiCtrl", COALESCE(m.kpi_v11_quality,0) as "kpiQual", COALESCE(m.kpi_v12_score,0) as "ked12", COALESCE(m.kpi_v12_rank,0) as "kedRank12", COALESCE(m.kpi_v12_discipline,0) as "kedDisc12", COALESCE(m.kpi_v12_legislation,0) as "kedLegis12", COALESCE(m.kpi_v12_efficiency,0) as "kedEff12", COALESCE(m.kpi_v12_committee,0) as "kedComm12", COALESCE(m.kpi_v12_requests,0) as "kedReq12", COALESCE(m.kpi_v12_impact,0) as "kedImpact12", COALESCE(m.shannon_diversity,0) as "shannon", COALESCE(m.adoption_rate,0) as "adoptionRate", m.signal_warnings, m.signal_strengths, m.signal_features FROM mps m WHERE ${whereSQL} ORDER BY ${sortCol} ${order} NULLS LAST LIMIT $${idx++} OFFSET $${idx++}`, [...params, limit, offset]);
    const countResult = (await q(`SELECT COUNT(*) as total FROM mps m WHERE ${whereSQL}`, params))[0];

    const result = deputies.map(d => ({
      ...d, conversion: d.totalBills > 0 ? Math.round((d.totalLaws / d.totalBills) * 100) : 0,
    }));

    json(res, { deputies: result, total: Number(countResult.total) });
  } catch (e) { error(res, e.message, 500); }
});

// --- FACTIONS ---
app.get('/api/factions', async (req, res) => {
  try {
    const results = await q('SELECT DISTINCT faction FROM mps WHERE faction IS NOT NULL AND faction != \'\' ORDER BY faction');
    json(res, { factions: results.map(r => r.faction) }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

// --- PLENARY SESSIONS ---
app.get('/api/plenary-sessions', async (req, res) => {
  try {
    const dates = await q('SELECT DISTINCT DATE(v.vote_date) as session_date FROM votes v WHERE v.bill_id IS NOT NULL ORDER BY v.vote_date DESC LIMIT 100');
    const sessions = [];
    for (const d of dates) {
      const bills = await q('SELECT b.bill_number, b.title FROM votes v JOIN bills b ON v.bill_id = b.id WHERE DATE(v.vote_date) = $1 LIMIT 20', [d.session_date]);
      sessions.push({ date: d.session_date, bills });
    }
    json(res, { sessions });
  } catch (e) { error(res, e.message, 500); }
});

// --- SCHEDULE ---
app.get('/api/schedule', async (req, res) => {
  try {
    const month = req.query.month;
    const year = req.query.year;
    const event_type = req.query.type;
    let query = 'SELECT * FROM rada_schedule WHERE 1=1';
    const params = [];
    let idx = 1;

    if (month) { query += ` AND date LIKE $${idx++}`; params.push(month + '%'); }
    else if (year) { query += ` AND date LIKE $${idx++}`; params.push(year + '%'); }
    else {
      const now = new Date();
      const y = now.getFullYear();
      const m = String(now.getMonth() + 1).padStart(2, '0');
      const m2 = String(now.getMonth() + 2).padStart(2, '0');
      const y2 = now.getMonth() === 11 ? y + 1 : y;
      query += ` AND ((date LIKE $${idx++}) OR (date LIKE $${idx++}))`;
      params.push(`${y}-${m}%`, `${y2}-${m2}%`);
    }
    if (event_type) { query += ` AND event_type = $${idx++}`; params.push(event_type); }
    query += ' ORDER BY date ASC';

    const schedule = await q(query, params);

    // meeting_date is ISO text — lexicographic compare works; show upcoming + last 30 days
    let cQuery = `SELECT *, to_char(meeting_date::date, 'DD.MM.YYYY') AS meeting_date_ua
                  FROM rada_committee_schedule WHERE meeting_date >= to_char(now() - interval '30 days', 'YYYY-MM-DD')`;
    const cParams = [];
    if (month) { cQuery += ` AND meeting_date LIKE $1`; cParams.push(month + '%'); }
    cQuery += ' ORDER BY meeting_date ASC LIMIT 100';
    const committees = cParams.length ? await q(cQuery, cParams) : await q(cQuery);

    const fresh = await q(`SELECT (SELECT max(updated_at) FROM rada_schedule) AS schedule_updated_at,
                                  (SELECT max(created_at) FROM rada_committee_schedule) AS committee_updated_at`);

    json(res, {
      schedule, committees,
      session: { convocation: 'IX скликання' },
      freshness: {
        scheduleUpdatedAt: fresh[0].schedule_updated_at,
        committeeUpdatedAt: fresh[0].committee_updated_at
      }
    }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

// --- ACTIVITY CALENDAR ---
app.get('/api/activity-calendar', async (req, res) => {
  try {
    const month = req.query.month;
    if (!month) return error(res, 'month param required (YYYY-MM)');
    const rows = await q(
      `SELECT to_char(date(created_at::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Kyiv'), 'YYYY-MM-DD') as day,
              COUNT(*) FILTER (WHERE change_type = 'new') as new_bills
       FROM change_log
       WHERE created_at >= $1 AND created_at < $2
       GROUP BY date(created_at::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Kyiv')`,
      [month + '-01', month + '-32']
    );
    const activity = {};
    for (const r of rows) {
      activity[r.day] = { new: Number(r.new_bills), changed: 0 };
    }
    // зміни — за ОФІЦІЙНОЮ датою події з хронології (bill_passings.pass_date),
    // а не за моментом виявлення нашим синком (інакше вечірні події з'їжджали на наступний день)
    const prows = await q(
      `SELECT left(pass_date,10) as day, COUNT(DISTINCT bill_id) as cnt
       FROM bill_passings
       WHERE left(pass_date,10) >= $1 AND left(pass_date,10) < $2
       GROUP BY 1`,
      [month + '-01', month + '-32']
    );
    for (const r of prows) {
      if (!activity[r.day]) activity[r.day] = { new: 0, changed: 0 };
      activity[r.day].changed = Number(r.cnt);
    }
    // votes per day (vote_date is ISO text)
    const vrows = await q(
      `SELECT left(vote_date,10) as day, count(*) as cnt FROM votes
       WHERE vote_date >= $1 AND vote_date < $2 GROUP BY 1`,
      [month + '-01', month + '-32']
    );
    for (const r of vrows) {
      if (!activity[r.day]) activity[r.day] = { new: 0, changed: 0 };
      activity[r.day].votes = Number(r.cnt);
    }
    // committee meetings per day
    const crows = await q(
      `SELECT meeting_date as day, count(*) as cnt FROM rada_committee_schedule
       WHERE meeting_date >= $1 AND meeting_date < $2 GROUP BY 1`,
      [month + '-01', month + '-32']
    );
    for (const r of crows) {
      if (!activity[r.day]) activity[r.day] = { new: 0, changed: 0 };
      activity[r.day].committee = Number(r.cnt);
    }
    // EU milestones per day
    const erows = await q(
      `SELECT to_char(event_date,'YYYY-MM-DD') as day, count(*) as cnt FROM eu_cluster_status
       WHERE event_date IS NOT NULL AND to_char(event_date,'YYYY-MM') = $1 GROUP BY 1`,
      [month]
    );
    for (const r of erows) {
      if (!activity[r.day]) activity[r.day] = { new: 0, changed: 0 };
      activity[r.day].eu = Number(r.cnt);
    }
    json(res, { activity }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

// --- ACTIVITY DAY DETAIL ---
app.get('/api/activity-day', async (req, res) => {
  try {
    const date = req.query.date;
    if (!date) return error(res, 'date param required (YYYY-MM-DD)');
    // «нові» — за моментом появи в моніторингу, «зміни» — за офіційною датою події (pass_date)
    const [newRows, passRows] = await Promise.all([
      q(`SELECT cl.change_type, b.bill_number, b.title, b.url, cl.old_value, cl.new_value
         FROM change_log cl
         JOIN bills b ON cl.bill_id = b.id
         WHERE cl.change_type = 'new'
           AND date(cl.created_at::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Kyiv') = $1
         ORDER BY b.bill_number`, [date]),
      q(`SELECT 'status_change' AS change_type, b.bill_number, b.title, b.url,
                NULL::text AS old_value, bp.status AS new_value
         FROM bill_passings bp
         JOIN bills b ON bp.bill_id = b.id
         WHERE left(bp.pass_date,10) = $1
         ORDER BY b.bill_number`, [date]),
    ]);
    const rows = [...newRows, ...passRows];
    const schedule = await q(
      `SELECT event_type, title, description, url FROM rada_schedule WHERE date = $1`, [date]
    );
    const committees = await q(
      `SELECT committee_name, meeting_time, topic, url FROM rada_committee_schedule WHERE meeting_date = $1`, [date]
    );
    const vot = await q(`SELECT count(*)::int as cnt FROM votes WHERE left(vote_date,10) = $1`, [date]);
    json(res, {
      date, changes: rows,
      events: { schedule, committees, votes: Number(vot[0]?.cnt || 0) }
    }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

// --- UNIFIED DASHBOARD (4 blocks in 1 call) ---
app.get('/api/dashboard', async (req, res) => {
  try {
    const now = new Date();
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const monthParam = `${now.getFullYear()}-${mm}`;
    const thirtyDaysAgo = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const d120 = new Date(now.getTime() - 120 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

    // Parallel queries for all blocks
    const [
      stats,
      schedule,
      activity,
      eu,
      topDeputies,
      topRiskyBills,
      euHarmonization,
      highRiskAlerts,
      activeBills
    ] = await Promise.all([
      // Block 1: General stats
      q('SELECT key, value FROM stats_cache').then(rows => {
        const cache = {};
        for (const r of rows) cache[r.key] = r.value;
        return {
          totalBills: Number(cache.total_bills) || 0,
          analyzedBills: Number(cache.analyzed_bills) || 0,
          totalMps: Number(cache.total_mps) || 0,
          activeMps: Number(cache.active_mps) || 0,
          highRisk: Number(cache.high_risk) || 0,
          mediumRisk: Number(cache.medium_risk) || 0,
          recentChanges: Number(cache.recent_changes) || 0,
          lastSync: cache.last_updated || null,
          newBills24h: Number(cache.new_bills_24h) || 0,
          statusChanges24h: Number(cache.status_changes_24h) || 0,
          totalVotes: Number(cache.total_votes) || 0,
        };
      }).catch(() => ({})),

      // Block 2: Schedule for current month
      q(`SELECT * FROM rada_schedule WHERE date LIKE $1 ORDER BY date`, [monthParam + '%']).catch(() => []),

      // Block 3: Activity calendar
      q(`SELECT to_char(date(created_at::timestamp AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Kyiv'), 'YYYY-MM-DD') as day,
              COUNT(*) FILTER (WHERE change_type = 'new') as new_bills,
              COUNT(*) FILTER (WHERE change_type = 'status_change') as status_changes
         FROM change_log
         WHERE created_at >= $1 AND created_at < $2
         GROUP BY date(created_at AT TIME ZONE 'UTC' AT TIME ZONE 'Europe/Kyiv')`,
        [monthParam + '-01', monthParam + '-32']
      ).then(rows => {
        const activity = {};
        for (const r of rows) activity[r.day] = { new: Number(r.new_bills), changed: Number(r.status_changes) };
        return activity;
      }).catch(() => ({})),

      // Block 4: EU alignment overall
      q(`SELECT overall_score, chapters_analyzed, total_chapters, calculated_at
         FROM eu_alignment_overall ORDER BY id DESC LIMIT 1`).then(rows => rows[0] || null).catch(() => null),

      // --- NEW BLOCK 1: Top 20 deputies by ІЕД ---
      q(`SELECT m.id, m.name, m.faction, m.end_date,
              COALESCE(m.kpi_v12_score, 0) as ked12,
              COALESCE(m.kpi_v12_rank, 0) as kedRank12,
              COALESCE(m.kpi_v12_discipline, 0) as kedDisc12,
              COALESCE(m.kpi_v12_legislation, 0) as kedLegis12,
              COALESCE(m.kpi_v12_efficiency, 0) as kedEff12,
              COALESCE(m.kpi_v12_committee, 0) as kedComm12,
              COALESCE(m.kpi_v12_requests, 0) as kedReq12,
              COALESCE(m.kpi_v12_impact, 0) as kedImpact12,
              COALESCE(m.eu_integration_score, 0) as euScore
         FROM mps m
         WHERE m.end_date IS NULL OR m.end_date = ''
         ORDER BY m.kpi_v12_score DESC NULLS LAST
         LIMIT 20`).catch(() => []),

      // --- NEW BLOCK 2: Top 10 risky bills (last 30 days) ---
      q(`SELECT b.id, b.bill_number, b.title, b.current_status, b.stage, b.updated_at,
              ra.toxicity, ra.risk_score, ra.significance, ra.impact, ra.risk_level, ra.json_data
         FROM bills b
         JOIN risk_assessments ra ON ra.bill_id = b.id
         WHERE b.updated_at >= $1
           AND (ra.toxicity > 0.7 OR ra.risk_score >= 4)
         ORDER BY ra.toxicity DESC NULLS LAST, ra.risk_score DESC NULLS LAST
         LIMIT 10`, [thirtyDaysAgo]).catch(() => []),

      // --- NEW BLOCK 3: EU Harmonization (6 clusters) ---
      q(`SELECT key, value FROM stats_cache WHERE key LIKE 'harmonization_cluster%' ORDER BY key`).then(rows => {
        const clusters = {};
        for (const r of rows) clusters[r.key] = parseFloat(r.value);
        return clusters;
      }).catch(() => ({})),

      // --- NEW BLOCK 4: Recent high-risk alerts ---
      q(`SELECT b.bill_number, b.title, b.current_status, ra.toxicity, ra.risk_score, ra.json_data, ra.assessed_at
         FROM risk_assessments ra
         JOIN bills b ON b.id = ra.bill_id
         WHERE ra.assessed_at >= $1
           AND (ra.toxicity > 0.7 OR ra.risk_score >= 4)
         ORDER BY ra.assessed_at DESC
         LIMIT 10`, [thirtyDaysAgo]).catch(() => []),

      // --- BLOCK 5: Active bills (last 120 days, threats=2) for threats table + counter ---
      q(`SELECT b.id, b.bill_number, b.title, b.current_status, b.stage, b.updated_at,
              ra.toxicity, ra.risk_score, ra.significance, ra.impact, ra.risk_level, ra.json_data, ra.has_analysis
         FROM bills b
         LEFT JOIN (SELECT bill_id, 1 as has_analysis, toxicity, risk_score, significance, impact, risk_level, json_data FROM risk_assessments) ra ON ra.bill_id = b.id
         WHERE b.updated_at >= $1
           AND (ra.toxicity > 0.7 OR ra.risk_score >= 4)
         ORDER BY b.updated_at DESC
         LIMIT 20`, [d120]).catch(() => []),
    ]);

    json(res, {
      stats,
      schedule,
      activity,
      eu,
      topDeputies,
      topRiskyBills,
      euHarmonization,
      highRiskAlerts,
      activeBills,
      month: monthParam,
    }, 200, 60);
  } catch (e) { error(res, e.message, 500); }
});

// --- EU ALIGNMENT ---
// EU integration index v1: cluster → acquis chapters (mirror of dashboard EU_CLUSTERS)
const EU_CLUSTER_CHAPTERS = {
  1: [1, 6, 27, 33],
  2: [2, 3, 4, 5, 7, 8, 9, 22, 23],
  3: [10, 16, 17, 18, 19, 20, 25, 26, 29, 30],
  4: [11, 12, 13, 14, 15, 21],
  5: [24, 28, 31],
  6: [32, 34, 35],
};

app.get('/api/eu-alignment', async (req, res) => {
  try {
    // Legacy data (kept for frontend compatibility)
    const overall = await q('SELECT * FROM eu_alignment_overall ORDER BY id DESC LIMIT 1');
    const chapters = await q('SELECT * FROM eu_alignment_chapters WHERE id IN (SELECT MAX(id) FROM eu_alignment_chapters GROUP BY chapter_id) ORDER BY chapter_id');

    // Get harmonization data from stats_cache
    const harmonization = {};
    const hKeys = ['harmonization_cluster1', 'harmonization_cluster2', 'harmonization_cluster3',
                    'harmonization_cluster4', 'harmonization_cluster5', 'harmonization_cluster6'];
    const hRows = await q(`SELECT key, value FROM stats_cache WHERE key IN (${hKeys.map((_, i) => `$${i+1}`).join(',')})`, hKeys);
    for (const r of hRows) {
      harmonization[r.key] = parseFloat(r.value);
    }

    // Calculate overall harmonization score (weighted by cluster importance)
    const weights = {1: 1.5, 2: 1.2, 3: 1.0, 4: 1.0, 5: 0.8, 6: 0.8};
    let weightedSum = 0, totalWeight = 0;
    for (let i = 1; i <= 6; i++) {
      const key = 'harmonization_cluster' + i;
      if (harmonization[key] !== undefined) {
        weightedSum += harmonization[key] * weights[i];
        totalWeight += weights[i];
      }
    }
    const harmonizationScore = totalWeight > 0 ? Math.round(weightedSum / totalWeight * 10) / 10 : 0;

    // Get total bills and signed for overall stats
    const totals = await q(`SELECT
      COUNT(*) as total_bills,
      COUNT(*) FILTER (WHERE stage = 4) as signed_bills
      FROM bills WHERE agenda_category IS NOT NULL`);
    const totalBills = totals[0] ? Number(totals[0].total_bills) : 0;
    const signedBills = totals[0] ? Number(totals[0].signed_bills) : 0;

    // --- v1: integration index from stats_cache ---
    const idxRows = await q(`SELECT value FROM stats_cache WHERE key = 'eu_integration_v1'`).catch(() => []);
    let index = null;
    if (idxRows[0]) {
      try {
        const raw = JSON.parse(idxRows[0].value);
        index = { value: raw.index, negotiation: raw.negotiation, legislation: raw.legislation, computedAt: raw.computed_at };
      } catch { /* malformed cache value */ }
    }

    // --- v1: cluster statuses + per-cluster harm (avg harmonization_ch over cluster chapters) ---
    const csRows = await q('SELECT cluster_id, status, event_date, source_url FROM eu_cluster_status ORDER BY cluster_id').catch(() => []);
    const chRows = await q(`SELECT key, value FROM stats_cache WHERE key LIKE 'harmonization_ch%'`).catch(() => []);
    const chHarm = {};
    for (const r of chRows) {
      const num = parseInt(r.key.replace('harmonization_ch', ''), 10);
      const v = parseFloat(String(r.value).split(':')[0]);
      if (!isNaN(num) && !isNaN(v)) chHarm[num] = v;
    }
    // node-pg parses DATE as local-midnight Date → read local Y/M/D to avoid UTC shift
    const fmtDate = d => {
      if (!d) return null;
      if (d instanceof Date) return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
      return String(d).slice(0, 10);
    };
    const clusters = csRows.map(c => {
      const chs = EU_CLUSTER_CHAPTERS[c.cluster_id] || [];
      const vals = chs.map(ch => chHarm[ch]).filter(v => v !== undefined);
      return {
        id: c.cluster_id,
        status: c.status,
        eventDate: fmtDate(c.event_date),
        sourceUrl: c.source_url,
        harm: vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length * 10) / 10 : 0,
      };
    });

    // --- v1: timeline of opened/closed clusters ---
    const timeline = csRows
      .filter(c => c.status !== 'not_opened')
      .map(c => ({ date: fmtDate(c.event_date), title: `Cluster ${c.cluster_id}: ${c.status}`, url: c.source_url }))
      .sort((a, b) => ((a.date || '') < (b.date || '') ? -1 : 1));

    // --- v1: latest EU news (eu_news_* keys written by sync_eu_tracker.py) ---
    const newsRows = await q(`SELECT value FROM stats_cache WHERE key LIKE 'eu_news_%' ORDER BY updated_at DESC LIMIT 8`).catch(() => []);
    const news = newsRows.map(r => {
      try { const n = JSON.parse(r.value); return { title: n.title, date: n.date, url: n.url }; }
      catch { return null; }
    }).filter(Boolean);

    // --- trend (same shape as /api/eu-alignment/trend) ---
    const trend = await q('SELECT calculated_at, weighted_score, overall_score, signed_score, in_process_score, signed_bills, in_process_bills FROM eu_alignment_overall ORDER BY calculated_at DESC LIMIT 30').catch(() => []);

    json(res, {
      // v1 shape
      index,
      clusters,
      timeline,
      news,
      trend,
      // backward-compatible fields
      overall: overall[0] || null,
      chapters,
      harmonization,
      harmonizationScore,
      totalBills,
      signedBills,
      lastUpdated: overall[0]?.calculated_at || null,
      signed: overall[0] ? {
        score: overall[0].signed_score || 0,
        bills: overall[0].signed_bills || 0
      } : null,
      inProcess: overall[0] ? {
        score: overall[0].in_process_score || 0,
        bills: overall[0].in_process_bills || 0
      } : null
    }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

app.get('/api/eu-alignment/trend', async (req, res) => {
  try {
    const trend = await q('SELECT calculated_at, weighted_score, overall_score, signed_score, in_process_score, signed_bills, in_process_bills FROM eu_alignment_overall ORDER BY calculated_at DESC LIMIT 30');
    json(res, { trend }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

app.get('/api/eu-alignment/chapter/:id', async (req, res) => {
  try {
    const chapterId = parseInt(req.params.id);
    if (isNaN(chapterId)) return error(res, 'Invalid chapter ID', 400);
    const history = await q('SELECT * FROM eu_alignment_chapters WHERE chapter_id = $1 ORDER BY calculated_at DESC LIMIT 30', [chapterId]);
    json(res, { chapter: history[0] || null, history }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

// --- INFO ATTACKS (Phase 3 digest + Phase 2 alerts) ---
app.get('/api/info-digest', async (req, res) => {
  try {
    const [digestRows, alerts, statsRows] = await Promise.all([
      q(`SELECT value FROM stats_cache WHERE key = 'info_digest'`).catch(() => []),
      q(`SELECT a.id, a.label, a.posts_count, a.channels_count, a.window_hours,
                a.debunk_url, a.detected_at, i.title AS sample
         FROM attack_alerts a
         LEFT JOIN info_items i ON i.id = a.first_item_id
         ORDER BY a.detected_at DESC LIMIT 10`).catch(() => []),
      q(`SELECT (SELECT count(*) FROM info_items
                 WHERE posted_at >= now() - interval '24 hours') AS items_24h,
                (SELECT count(DISTINCT source_name) FROM info_items
                 WHERE source_type = 'telegram'
                   AND posted_at >= now() - interval '24 hours') AS tg_channels_active,
                (SELECT count(*) FROM attack_alerts) AS attacks_total`),
    ]);
    let digest = null;
    if (digestRows[0]) { try { digest = JSON.parse(digestRows[0].value); } catch { /* malformed cache */ } }
    json(res, { digest, alerts, stats: statsRows[0] || {} }, 200, 300);
  } catch (e) { error(res, e.message, 500); }
});

app.get('/api/voting-clubs', async (req, res) => {
  try {
    const mp = parseInt(req.query.mp, 10);
    if (mp) {
      const allies = await q(`SELECT CASE WHEN va.mp_a = $1 THEN va.mp_b ELSE va.mp_a END AS ally_id,
                                    m.name, m.faction, va.pct, va.common, va.cross_faction
                             FROM voting_allies va
                             JOIN mps m ON m.id = CASE WHEN va.mp_a = $1 THEN va.mp_b ELSE va.mp_a END
                             WHERE va.mp_a = $1 OR va.mp_b = $1
                             ORDER BY va.pct DESC, va.common DESC LIMIT $2`, [mp, Math.min(parseInt(req.query.limit, 10) || 10, 50)]);
      return json(res, { allies: allies.map(a => ({ id: a.ally_id, name: a.name, faction: a.faction,
        pct: Number(a.pct), common: Number(a.common), crossFaction: a.cross_faction })) }, 200, 600);
    }
    const limit = Math.min(parseInt(req.query.limit, 10) || 100, 300);
    const [metaRows, pairRows] = await Promise.all([
      q(`SELECT value FROM stats_cache WHERE key = 'voting_clubs_meta'`).catch(() => []),
      q(`SELECT va.mp_a, ma.name AS a_name, ma.faction AS a_faction,
                va.mp_b, mb.name AS b_name, mb.faction AS b_faction, va.pct, va.common
         FROM voting_allies va
         JOIN mps ma ON ma.id = va.mp_a
         JOIN mps mb ON mb.id = va.mp_b
         WHERE va.cross_faction
         ORDER BY va.pct DESC, va.common DESC LIMIT $1`, [limit]),
    ]);
    let meta = null;
    if (metaRows[0]) { try { meta = JSON.parse(metaRows[0].value); } catch { /* malformed cache */ } }
    json(res, {
      meta,
      pairs: pairRows.map(p => ({
        a: { id: p.mp_a, name: p.a_name, faction: p.a_faction },
        b: { id: p.mp_b, name: p.b_name, faction: p.b_faction },
        pct: Number(p.pct), common: Number(p.common),
      })),
    }, 200, 600);
  } catch (e) { error(res, e.message, 500); }
});

app.get('/api/interests', async (req, res) => {
  try {
    const mp = parseInt(req.query.mp, 10);
    if (!mp) return error(res, 'mp parameter required', 400);
    const rows = await q(`SELECT di.sector, di.authored, di.voted_for, di.voted_against
                          FROM deputy_interests di WHERE di.mp_id = $1
                          ORDER BY (di.authored * 3 + di.voted_for) DESC LIMIT 16`, [mp]);
    json(res, {
      sectors: rows.map(r => ({ sector: r.sector, authored: Number(r.authored),
        votedFor: Number(r.voted_for), votedAgainst: Number(r.voted_against) })),
    }, 200, 600);
  } catch (e) { error(res, e.message, 500); }
});

app.get('/api/lobbying', async (req, res) => {
  try {
    const bill = (req.query.bill || '').toString().replace(/[^\d]/g, '');
    if (bill) {
      const rows = await q(`SELECT ls.name AS subject_name, lo.sphere, lo.subject_of_lobbying,
                                   lo.government_agency, lo.agency_representative, lo.last_interaction
                            FROM lobbying_objects lo JOIN lobbying_subjects ls ON ls.guid = lo.subject_guid
                            WHERE lo.bill_number = $1
                            ORDER BY lo.last_interaction DESC NULLS LAST`, [bill]);
      return json(res, { count: rows.length, objects: rows }, 200, 600);
    }
    const limit = Math.min(parseInt(req.query.limit, 10) || 20, 100);
    const [metaRows, topRows, recentRows] = await Promise.all([
      q(`SELECT value FROM stats_cache WHERE key = 'lobbying_registry_meta'`).catch(() => []),
      q(`SELECT ls.name, count(*) AS objects, count(lo.bill_number) AS bills_linked
         FROM lobbying_objects lo JOIN lobbying_subjects ls ON ls.guid = lo.subject_guid
         GROUP BY 1 ORDER BY objects DESC LIMIT 8`),
      q(`SELECT ls.name AS subject_name, lo.sphere, lo.subject_of_lobbying,
                lo.government_agency, lo.bill_number, lo.last_interaction, bb.id AS bill_id
         FROM lobbying_objects lo JOIN lobbying_subjects ls ON ls.guid = lo.subject_guid
         LEFT JOIN bills bb ON bb.bill_number = lo.bill_number
         WHERE lo.bill_number IS NOT NULL
         ORDER BY lo.last_interaction DESC NULLS LAST LIMIT ${limit}`),
    ]);
    let meta = null;
    if (metaRows[0]) { try { meta = JSON.parse(metaRows[0].value); } catch { /* malformed cache */ } }
    json(res, {
      meta,
      topSubjects: topRows.map(r => ({ name: r.name, objects: Number(r.objects), billsLinked: Number(r.bills_linked) })),
      recent: recentRows.map(r => ({
        subjectName: r.subject_name, sphere: r.sphere, text: r.subject_of_lobbying,
        agency: r.government_agency, billNumber: r.bill_number, lastInteraction: r.last_interaction,
        billId: r.bill_id,
      })),
    }, 200, 600);
  } catch (e) { error(res, e.message, 500); }
});

app.get('/api/declarations', async (req, res) => {
  try {
    const mp = parseInt(req.query.mp, 10);
    if (!mp) return error(res, 'mp parameter required', 400);
    const rows = await q(`SELECT uuid, submitted_at, declaration_year, companies
                          FROM deputy_declarations WHERE mp_id = $1`, [mp]);
    const d = rows[0];
    json(res, d ? {
      uuid: d.uuid, submittedAt: d.submitted_at, year: d.declaration_year,
      companies: (d.companies || []).map(c => ({
        name: c.name || '', edrpou: c.edrpou || '', legalForm: c.legalForm || '',
        sharePct: c.share_pct ?? null, sector: c.sector || '',
      })),
    } : { companies: [] }, 200, 600);
  } catch (e) { error(res, e.message, 500); }
});

// --- Query endpoints REMOVED (security: no raw SQL exposure) ---
// --- /api/eu-alignment/bills REMOVED 2026-08-21: bill_eu_classification table dropped (feature never populated) ---

app.use((req, res) => error(res, 'Not found', 404));

const PORT = process.env.PORT || 8788;
app.listen(PORT, () => console.log(`API server on port ${PORT}`));
