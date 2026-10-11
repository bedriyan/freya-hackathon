"""Generates out/brief-a.workflow.json. Every rule and value here comes from briefs/brief-a.md and tools/openapi.json."""
import json, pathlib, sys

BASE = "https://freya-bank-mock.freyavoice.workers.dev/v1/"
HOLIDAYS = ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"]
# reason -> outcome, from the closing table of the brief
REASON_OUTCOME = {"refused": "no_promise", "hardship": "no_promise", "dispute": "no_promise", "service_error": "no_promise",
                  "failed": "not_verified", "declined": "not_verified",
                  "callback": "not_reached", "no_callback": "not_reached", "wrong_number": "not_reached", "deceased": "not_reached", "unclear": "not_reached",
                  "restructuring": "transferred", "human": "transferred", "support": "transferred"}
QUEUE_REASON = {"restructuring": "restructuring", "general": "human", "support": "support"}
OUTCOMES = ["promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"]


def pydict(d):
    return "dict(" + ", ".join(f"{k}={v!r}" for k, v in d.items()) + ")"


# ---------------------------------------------------------------- code: setup (first step)
SETUP = """
import datetime
try:
    vars['cp_fail'] = 0
    vars['promise_state'] = 'none'
    vars['id_state'] = 'pending'
    vars['id_dob_ok'] = 'no'
    vars['transfer_queue'] = 'none'
    vars['close_state'] = 'open'
    vars['account_state'] = 'pending'
    vars['callback_facts'] = 'Callback facts are not available. A callback is only possible today or on the next business day (Monday to Friday, no bank holiday), from 10:00 to 19:00, later than now.'
    HOL = __HOL__
    WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    MN = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    s = str(vars.get('now') or '').strip()
    try:
        now = datetime.datetime.fromisoformat(s[:16])
    except Exception:
        now = datetime.datetime.fromisoformat(s[:10] + 'T12:00')
    today = now.date()
    one = datetime.timedelta(days=1)
    hm = now.strftime('%H:%M')
    nb = today + one
    while nb.weekday() > 4 or nb.isoformat() in HOL:
        nb = nb + one
    tom = today + one
    t_txt = WD[today.weekday()] + ' ' + str(today.day) + ' ' + MN[today.month - 1] + ' ' + str(today.year) + ' (' + today.isoformat() + ')'
    n_txt = WD[nb.weekday()] + ' ' + str(nb.day) + ' ' + MN[nb.month - 1] + ' ' + str(nb.year) + ' (' + nb.isoformat() + ')'
    m_txt = WD[tom.weekday()] + ' ' + str(tom.day) + ' ' + MN[tom.month - 1] + ' ' + str(tom.year) + ' (' + tom.isoformat() + ')'
    today_ok = today.weekday() < 5 and today.isoformat() not in HOL and hm < '19:00'
    lines = []
    lines.append('NOW is ' + t_txt + ', ' + hm + '.')
    if today_ok:
        if hm < '10:00':
            lines.append('ALLOWED SLOT 1: today, ' + t_txt + ', from 10:00 to 19:00 (both included).')
        else:
            lines.append('ALLOWED SLOT 1: today, ' + t_txt + ', any time later than ' + hm + ' up to 19:00 (19:00 included).')
    else:
        lines.append('Today is NOT possible for a callback.')
    lines.append('ALLOWED SLOT ' + ('2' if today_ok else '1') + ': the next business day, ' + n_txt + ', from 10:00 to 19:00 (both included).')
    lines.append('Everything else is OUTSIDE the window: any other day, any time before 10:00 or after 19:00, a weekend day, a bank holiday.')
    if tom == nb:
        lines.append('"Tomorrow" is ' + m_txt + ', which is the next business day.')
    else:
        lines.append('"Tomorrow" is ' + m_txt + ', which is NOT a business day, so it is OUTSIDE the window.')
    vars['callback_facts'] = '\\n'.join(lines)
    vars['cb_today'] = today.isoformat()
    vars['cb_today_ok'] = 'yes' if today_ok else 'no'
    vars['cb_now_hm'] = hm
    vars['cb_next'] = nb.isoformat()
except Exception:
    pass
""".replace("__HOL__", repr(HOLIDAYS))

# ---------------------------------------------------------------- code: account facts
COMPUTE = """
import datetime, json, re
try:
    vars['account_state'] = 'error'
    vars['account_facts'] = 'Account facts are not available.'
    vars['product_words'] = 'account'
    vars['full_text'] = 'not available'
    vars['days_text'] = 'some days'
    HOL = __HOL__
    WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    MN = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
    s = str(vars.get('now') or '').strip()
    try:
        now = datetime.datetime.fromisoformat(s[:16])
    except Exception:
        now = datetime.datetime.fromisoformat(s[:10] + 'T12:00')
    today = now.date()
    one = datetime.timedelta(days=1)
    prod = None
    cands = []
    for k in ['pf_json', 'products', 'get_account_response.data.products', 'get_account_response.data']:
        if vars.get(k) is not None:
            cands.append(vars.get(k))
    for k in list(vars.keys()):
        if str(k).endswith('products') and vars.get(k) is not None:
            cands.append(vars.get(k))
    for c in cands:
        v = c
        if isinstance(v, str):
            t = v.strip()
            try:
                v = json.loads(t)
            except Exception:
                try:
                    import ast
                    v = ast.literal_eval(t)
                except Exception:
                    v = None
        if isinstance(v, dict) and 'products' in v:
            v = v.get('products')
        if isinstance(v, dict) and 'past_due' in v:
            v = [v]
        if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict) and 'past_due' in v[0]:
            prod = v[0]
            break
    if prod is None:
        flat = dict()
        for k in list(vars.keys()):
            m = re.search(r'products[.\\[]0\\]?[.](\\w+)$', str(k))
            if m:
                flat[m.group(1)] = vars.get(k)
        if 'past_due' in flat:
            prod = flat
    ptype = str(prod.get('type') or '')
    full = round(float(prod.get('past_due')), 2)
    days = int(float(prod.get('days_late')))
    if ptype == 'credit_card':
        words = 'credit card ending ' + str(prod.get('card_last4') or '')
        fl = prod.get('minimum_payment')
        floor_name = 'minimum payment'
    else:
        words = 'car loan' if ptype == 'car_loan' else 'personal loan'
        fl = prod.get('instalment_amount')
        floor_name = 'instalment amount'
    if fl is None:
        fl = prod.get('minimum_payment')
    if fl is None:
        fl = prod.get('instalment_amount')
    if fl is None:
        fl = full
    floor = round(float(fl), 2)
    late = days >= 8
    need = 7 if late else 12
    d = today
    cnt = 0
    while cnt < need:
        d = d + one
        if d.weekday() < 5 and d.isoformat() not in HOL:
            cnt = cnt + 1
    deadline = d
    mon = today - datetime.timedelta(days=today.weekday())
    a1 = None
    wk = 0
    while a1 is None and wk < 6:
        d = mon + datetime.timedelta(days=7 * wk + 4)
        lo = mon + datetime.timedelta(days=7 * wk)
        while d >= lo:
            if d.weekday() < 5 and d.isoformat() not in HOL:
                break
            d = d - one
        if d >= lo and d > today:
            a1 = d
        wk = wk + 1
    if a1 is None or a1 > deadline:
        a1 = deadline
    nice = dict()
    stat = dict()
    okd = []
    d = mon - one
    end = deadline + datetime.timedelta(days=45)
    while d <= end:
        iso = d.isoformat()
        nice[iso] = WD[d.weekday()] + ' ' + str(d.day) + ' ' + MN[d.month - 1] + ' ' + str(d.year) + ' (' + iso + ')'
        d = d + one
    d = mon - one
    while d <= deadline + datetime.timedelta(days=30):
        iso = d.isoformat()
        if d < today:
            stat[iso] = 'IN THE PAST, cannot be used'
        elif d > deadline:
            stat[iso] = 'AFTER THE DEADLINE -> say the latest date you can record is ' + nice[deadline.isoformat()] + ' and offer it'
        elif d.weekday() < 5 and iso not in HOL:
            stat[iso] = 'OK'
            okd.append(iso)
        else:
            n = d + one
            while n.weekday() > 4 or n.isoformat() in HOL:
                n = n + one
            why = 'bank holiday' if iso in HOL else 'weekend'
            stat[iso] = 'NOT ALLOWED (' + why + ') -> offer the next business day, ' + nice[n.isoformat()]
        d = d + one
    ft = '{:,.2f}'.format(full)
    flt = '{:,.2f}'.format(floor)
    L = []
    L.append('TODAY = ' + nice[today.isoformat()])
    L.append('PRODUCT = ' + words)
    L.append('FULL = ' + ft + ' (the full past-due amount)')
    L.append('FLOOR = ' + flt + ' (the ' + floor_name + ', the lowest amount that can be recorded)')
    L.append('DAYS LATE = ' + str(days))
    L.append('DEADLINE = ' + nice[deadline.isoformat()] + ' (the latest date a promise can have)')
    L.append('ATTEMPTS ALLOWED = ' + ('2' if late else '1'))
    L.append('ATTEMPT 1 DATE = ' + nice[a1.isoformat()])
    if late:
        L.append('ATTEMPT 2 = the FLOOR ' + flt + ' on the DEADLINE ' + nice[deadline.isoformat()])
    else:
        L.append('ATTEMPT 2 = does not exist for this customer')
    L.append('')
    L.append('CALENDAR (status of each day as a promise date):')
    d = today
    while d <= deadline:
        iso = d.isoformat()
        L.append('- ' + nice[iso] + ': ' + stat[iso] + (' (today)' if d == today else '') + (' (this is the DEADLINE)' if d == deadline else ''))
        d = d + one
    L.append('- every day after ' + deadline.isoformat() + ': ' + stat[(deadline + one).isoformat()])
    L.append('')
    L.append('WEEKDAY WORDS (then check the day in the rule above or in CALENDAR):')
    tom = today + one
    L.append('- tomorrow = ' + nice[tom.isoformat()] + ': ' + stat[tom.isoformat()])
    i = 0
    while i < 7:
        a = mon + datetime.timedelta(days=i)
        b = mon + datetime.timedelta(days=7 + i)
        L.append('- "this ' + WD[i] + '" = ' + nice[a.isoformat()] + ': ' + stat[a.isoformat()])
        L.append('- "next ' + WD[i] + '" = ' + nice[b.isoformat()] + ': ' + stat[b.isoformat()])
        i = i + 1
    L.append('- a weekday said without "this" or "next" means the first such day after today.')
    L.append('- "the week after next", "next week", "end of the month", "when my salary comes" are NOT exact days.')
    vars['account_facts'] = '\\n'.join(L)
    vars['product_id'] = str(prod.get('product_id') or 'p1')
    vars['product_words'] = words
    vars['full_text'] = ft
    vars['floor_text'] = flt
    vars['full_amount'] = str(full)
    vars['floor_amount'] = str(floor)
    vars['days_text'] = str(days) + (' day' if days == 1 else ' days')
    vars['ok_dates'] = ','.join(okd)
    vars['deadline_iso'] = deadline.isoformat()
    vars['account_state'] = 'ok'
except Exception:
    pass
""".replace("__HOL__", repr(HOLIDAYS))

# ---------------------------------------------------------------- hooks
GET_ACCOUNT_POST = """
try:
    import json
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if isinstance(d, dict):
            ps = d.get('products')
            if isinstance(ps, list) and len(ps) > 0 and isinstance(ps[0], dict):
                d['pf_json'] = json.dumps(ps[0])
                try:
                    vars['pf_json'] = d['pf_json']
                except Exception:
                    pass
except Exception:
    pass
"""

VERIFY_PRE = """
try:
    import re, datetime
    b = config.get('body')
    if isinstance(b, dict):
        f = str(b.get('field') or '').strip()
        a = str(b.get('answer') or '').strip()
        dob_ok = ''
        try:
            dob_ok = str(vars.get('id_dob_ok') or '')
        except Exception:
            dob_ok = ''
        if dob_ok == 'yes':
            f = 'phone_last4'
        if f not in ('date_of_birth', 'phone_last4'):
            f = 'phone_last4' if re.match(r'^\\D*\\d{4}\\D*$', a) else 'date_of_birth'
        if f == 'phone_last4':
            digs = re.sub(r'\\D', '', a)
            if len(digs) >= 4:
                a = digs[-4:]
            elif digs:
                a = digs
        else:
            MN = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
            t = a.lower().replace(',', ' ')
            t = re.sub(r'(\\d)(st|nd|rd|th)\\b', r'\\1', t)
            t = re.sub(r'\\bof\\b', ' ', t)
            y = 0
            mo = 0
            dd = 0
            m = re.match(r'^\\s*(\\d{4})[-/.](\\d{1,2})[-/.](\\d{1,2})\\s*$', t)
            if m:
                y = int(m.group(1)); mo = int(m.group(2)); dd = int(m.group(3))
            else:
                m = re.match(r'^\\s*(\\d{1,2})[-/. ]+(\\d{1,2})[-/. ]+(\\d{4})\\s*$', t)
                if m:
                    dd = int(m.group(1)); mo = int(m.group(2)); y = int(m.group(3))
                else:
                    i = 0
                    for name in MN:
                        i = i + 1
                        if re.search(r'\\b' + name, t):
                            mo = i
                    nums = re.findall(r'\\d+', t)
                    if mo and len(nums) == 2:
                        n1 = int(nums[0]); n2 = int(nums[1])
                        if n1 > 31:
                            y = n1; dd = n2
                        else:
                            dd = n1; y = n2
                    else:
                        mo = 0
            if y and mo and dd:
                try:
                    a = datetime.date(y, mo, dd).isoformat()
                except Exception:
                    pass
        b['field'] = f
        b['answer'] = a
        try:
            vars['vi_field'] = f
        except Exception:
            pass
except Exception:
    pass
"""

VERIFY_POST = """
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        fld = ''
        try:
            fld = str(vars.get('vi_field') or '')
        except Exception:
            fld = ''
        if not isinstance(d, dict):
            d = dict(ok=False, error='the service failed, nothing was counted')
            result['data'] = d
            nxt = 'The service failed. This is not a wrong answer. Call verify_identity again now with the same field and the same answer.'
        elif d.get('all_verified') is True:
            nxt = 'Both answers are correct. Say only: "Thank you, one moment please."'
            try:
                vars['id_state'] = 'verified'
            except Exception:
                pass
        elif d.get('verified') is True:
            nxt = 'This answer is correct. Now ask for the last four digits of the phone number on file (if that is the one already answered, ask the question that is still open).'
            try:
                if fld == 'date_of_birth':
                    vars['id_dob_ok'] = 'yes'
            except Exception:
                pass
        elif d.get('hint') == 'day_month_swapped':
            nxt = 'Do NOT say the answer was wrong. Ask the customer to say the date of birth again with the month as a word. This does not count as a wrong answer. Then call verify_identity with the new answer.'
        elif d.get('ok') is False:
            nxt = 'The answer could not be read. This is NOT a wrong answer. Ask the customer to say it again (a date with the month as a word), then call verify_identity again: date as YYYY-MM-DD, phone as four digits.'
        else:
            left = d.get('wrong_answers_left')
            if str(left) == '0':
                nxt = 'The identity check has FAILED. Say nothing about the account. Tell the customer to use the mobile app or visit a branch, thank them, say goodbye, and call close_call with outcome not_verified and reason failed.'
                try:
                    vars['id_state'] = 'failed'
                except Exception:
                    pass
            else:
                nxt = 'WRONG answer. Tell the customer the answer does not match and ask the SAME question again. Wrong answers left: ' + str(left) + '. Call verify_identity with the new answer.'
        d['what_to_do_next'] = nxt
except Exception:
    pass
"""

PROMISE_PRE = """
try:
    import datetime
    b = config.get('body')
    if isinstance(b, dict):
        problem = ''
        pid = str(vars.get('product_id') or '')
        if pid:
            b['product_id'] = pid
        try:
            dt = datetime.date.fromisoformat(str(b.get('date')).strip()[:10])
            b['date'] = dt.isoformat()
            okd = str(vars.get('ok_dates') or '')
            if okd and dt.isoformat() not in okd.split(','):
                problem = 'the date ' + dt.isoformat() + ' is not an allowed promise date (it must be an OK day in CALENDAR: today or later, a business day, not after the DEADLINE)'
        except Exception:
            problem = 'the date must be sent as YYYY-MM-DD'
        try:
            am = round(float(str(b.get('amount')).replace(',', '')), 2)
            b['amount'] = am
            lo = float(vars.get('floor_amount'))
            hi = float(vars.get('full_amount'))
            if am < lo - 0.005 or am > hi + 0.005:
                problem = 'the amount ' + str(am) + ' is not between the FLOOR and the FULL amount'
        except Exception:
            pass
        vars['cp_block'] = problem
        if problem:
            config['url'] = ''
except Exception:
    pass
"""

PROMISE_POST = """
try:
    if isinstance(result, dict):
        result.pop('header', None)
        blk = ''
        try:
            blk = str(vars.get('cp_block') or '')
        except Exception:
            blk = ''
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict(ok=False, error='the service failed, nothing was recorded')
            result['data'] = d
        if d.get('ok') is True:
            nxt = 'The promise IS recorded. Say: "Your promise is recorded. You will receive a text message confirming this promise."'
            try:
                vars['promise_state'] = 'recorded'
            except Exception:
                pass
        elif blk:
            d['ok'] = False
            d['error'] = 'not sent to the bank: ' + blk
            nxt = 'NOT recorded, and this is not a service failure: ' + blk + '. Do not say it is recorded. Look at FACTS, tell the customer the correct option, read it back again, get a clear yes, then call create_promise again.'
        else:
            n = 0
            try:
                n = int(float(vars.get('cp_fail') or 0)) + 1
                vars['cp_fail'] = n
            except Exception:
                n = 0
            if n == 1:
                nxt = 'The service FAILED. NOTHING is recorded. Do not say it is recorded. Call create_promise again right now with exactly the same amount and date.'
            elif n >= 2:
                nxt = 'The service FAILED again. NOTHING is recorded. Tell the customer you could not record the promise, thank them, say goodbye, and call close_call with outcome no_promise and reason service_error.'
            else:
                nxt = 'The service FAILED. NOTHING is recorded. Do not say it is recorded. If this was the first failure, call create_promise again now with the same values. If it was the second failure, tell the customer you could not record the promise, thank them, say goodbye, and call close_call with outcome no_promise and reason service_error.'
        d['what_to_do_next'] = nxt
except Exception:
    pass
"""

CALLBACK_PRE = """
try:
    import re, datetime
    b = config.get('body')
    if isinstance(b, dict):
        problem = ''
        t = str(b.get('time') or '').strip().lower()
        m = re.match(r'^(\\d{1,2})(?:[:.h](\\d{2}))?(?::\\d{2})?\\s*(am|pm)?$', t)
        if m:
            hh = int(m.group(1))
            mm = int(m.group(2) or 0)
            if m.group(3) == 'pm' and hh < 12:
                hh = hh + 12
            t = ('0' + str(hh))[-2:] + ':' + ('0' + str(mm))[-2:]
            b['time'] = t
        else:
            problem = 'the time must be sent as HH:MM'
        try:
            dt = datetime.date.fromisoformat(str(b.get('date')).strip()[:10]).isoformat()
            b['date'] = dt
        except Exception:
            dt = ''
            problem = 'the date must be sent as YYYY-MM-DD'
        cbt = str(vars.get('cb_today') or '')
        cbn = str(vars.get('cb_next') or '')
        if not problem and cbt and cbn:
            if t < '10:00' or t > '19:00':
                problem = 'the time ' + t + ' is outside 10:00 to 19:00'
            elif dt == cbt:
                if str(vars.get('cb_today_ok')) != 'yes' or t <= str(vars.get('cb_now_hm') or ''):
                    problem = 'today at ' + t + ' is not later than now or today is not possible'
            elif dt != cbn:
                problem = 'the date ' + dt + ' is neither today nor the next business day'
        vars['cb_block'] = problem
        if problem:
            config['url'] = ''
except Exception:
    pass
"""

CALLBACK_POST = """
try:
    if isinstance(result, dict):
        result.pop('header', None)
        blk = ''
        try:
            blk = str(vars.get('cb_block') or '')
        except Exception:
            blk = ''
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict(ok=False, error='the service failed, nothing was scheduled')
            result['data'] = d
        if d.get('ok') is True:
            nxt = 'The callback IS scheduled. Thank the person, say goodbye, and call close_call with outcome not_reached and reason callback.'
        elif blk:
            d['ok'] = False
            d['error'] = 'not sent to the bank: ' + blk
            nxt = 'NOT scheduled: ' + blk + '. That time is OUTSIDE the callback window. Follow the rule for a time outside the window.'
        else:
            nxt = 'The service FAILED. NOTHING is scheduled. Do not say it is scheduled. Call schedule_callback once more with the same date and time. If it fails again, say you could not schedule the callback, thank the person, say goodbye, and call close_call with outcome not_reached and reason no_callback.'
        d['what_to_do_next'] = nxt
except Exception:
    pass
"""

TRANSFER_PRE = """
try:
    b = config.get('body')
    if isinstance(b, dict):
        vars['tq_pending'] = str(b.get('queue') or '')
except Exception:
    pass
"""

TRANSFER_POST = """
try:
    if isinstance(result, dict):
        result.pop('header', None)
        R = __QR__
        q = ''
        try:
            q = str(vars.get('tq_pending') or '')
        except Exception:
            q = ''
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict(ok=False, error='the service failed, the call was not transferred')
            result['data'] = d
        if d.get('ok') is True:
            nxt = 'The transfer IS done. Tell the customer you are connecting them now, thank them, say goodbye, and call close_call with outcome transferred' + ((' and reason ' + R[q]) if q in R else ' and the reason that belongs to this queue') + '.'
            try:
                if q in R:
                    vars['transfer_queue'] = q
            except Exception:
                pass
        else:
            nxt = 'The transfer FAILED. Do not say the customer is being connected. Call transfer_to_agent once more with the same queue.'
        d['what_to_do_next'] = nxt
except Exception:
    pass
""".replace("__QR__", pydict(QUEUE_REASON))

CLOSE_PRE = """
try:
    b = config.get('body')
    if isinstance(b, dict):
        M = __RO__
        R = __QR__
        o = str(b.get('outcome') or '').strip().lower()
        r = str(b.get('reason') or '').strip().lower()
        if r in ('none', 'null', '-'):
            r = ''
        st = ''
        q = ''
        try:
            st = str(vars.get('promise_state') or '')
            q = str(vars.get('transfer_queue') or '')
        except Exception:
            pass
        if q in R:
            o = 'transferred'
            r = R[q]
        elif st == 'recorded':
            o = 'promise'
            r = ''
        elif o == 'paid':
            r = ''
        elif r in M:
            o = M[r]
        b['outcome'] = o
        if o in ('promise', 'paid') or not r:
            b.pop('reason', None)
        else:
            b['reason'] = r
except Exception:
    pass
""".replace("__RO__", pydict(REASON_OUTCOME)).replace("__QR__", pydict(QUEUE_REASON))

CLOSE_POST = """
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict(ok=False, error='the service failed, the call is not closed')
            result['data'] = d
        if d.get('ok') is True:
            d['what_to_do_next'] = 'The call is closed. Say only: "Goodbye."'
            try:
                vars['close_state'] = 'closed'
            except Exception:
                pass
        else:
            d['what_to_do_next'] = 'close_call FAILED. Call close_call again now with the correct outcome and reason. Say nothing else.'
except Exception:
    pass
"""

for name, code in list(globals().items()):
    if name.isupper() and isinstance(code, str) and "try:" in code:
        assert "{{" not in code and "}}" not in code, name
        compile(code, name, "exec")


# ---------------------------------------------------------------- tool builders
def api_tool(name, desc, fields, required, pre="", post="", retries=0, mapping=None):
    body = {"customer_id": {"type": "string", "default": "{{customer_id}}"}}
    body.update(fields)
    t = {"type": "function", "config": {"timeoutMs": 12000, "maxRetries": retries},
         "function": {"name": name, "handler": "__function__:api_call", "description": desc,
                      "parameters": {"type": "object", "properties": {
                          "url": {"type": "string", "default": BASE + name},
                          "method": {"type": "string", "default": "POST"},
                          "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                          "body": {"type": "object", "properties": body, "required": required}}}}}
    if pre: t["pre_process_code"] = pre
    if post: t["post_process_code"] = post
    if mapping: t["response_mapping"] = mapping
    return t


def verify_tool():
    return api_tool("verify_identity",
                    "Check one security answer with the bank. CALL it every time the customer gives an answer to a security question. Never decide yourself whether an answer is right.",
                    {"field": {"type": "string", "enum": ["date_of_birth", "phone_last4"], "default": "{{field}}", "description": "Which question was answered. One of: date_of_birth, phone_last4."},
                     "answer": {"type": "string", "default": "{{answer}}", "description": "date_of_birth: the date as YYYY-MM-DD (convert what the customer said; a date said only in numbers is day, month, year). phone_last4: exactly four digits."}},
                    ["field", "answer"], VERIFY_PRE, VERIFY_POST)


def promise_tool():
    return api_tool("create_promise",
                    "Record the payment promise. CALL it only after you read back product, amount and date, asked \"Do you confirm?\" and the customer clearly said yes. Saying that it is recorded is not enough: you must call it.",
                    {"product_id": {"type": "string", "default": "{{product_id}}"},
                     "amount": {"type": "number", "default": "{{amount}}", "description": "The amount the customer confirmed, as a number, for example 637.5"},
                     "date": {"type": "string", "default": "{{date}}", "description": "The confirmed payment date as YYYY-MM-DD, copied from CALENDAR."}},
                    ["product_id", "amount", "date"], PROMISE_PRE, PROMISE_POST, mapping=[{"path": "data.promise_id", "variable": "promise_id"}])


def callback_tool():
    return api_tool("schedule_callback",
                    "Book a callback for the customer. CALL it when someone else answered and gave a day and a time that fit an ALLOWED SLOT.",
                    {"date": {"type": "string", "default": "{{date}}", "description": "Callback date as YYYY-MM-DD, copied from the allowed slots."},
                     "time": {"type": "string", "default": "{{time}}", "description": "Callback time as HH:MM, 24 hour clock, for example 11:00"}},
                    ["date", "time"], CALLBACK_PRE, CALLBACK_POST)


def transfer_tool(queues):
    return api_tool("transfer_to_agent",
                    "Hand the call to a person. CALL it when a rule says to transfer. Queues: restructuring = new payment plan or lower instalments; support = customer support team after illness, a death in the family or job loss; general = the customer insists on a human.",
                    {"queue": {"type": "string", "enum": queues, "default": "{{queue}}", "description": "One of: " + ", ".join(queues) + "."}},
                    ["queue"], TRANSFER_PRE, TRANSFER_POST)


def close_tool(outcomes, reasons):
    return api_tool("close_call",
                    "End the call and record how it ended. CALL it exactly once, as the very last thing, in the same turn in which you thank the customer and say goodbye. Nothing can be done after it.",
                    {"outcome": {"type": "string", "enum": outcomes, "default": "{{outcome}}", "description": "One of: " + ", ".join(outcomes) + "."},
                     "reason": {"type": "string", "enum": reasons, "default": "{{reason}}", "description": "One of: " + ", ".join(reasons) + ". Leave it out for outcome promise and outcome paid."}},
                    ["outcome"], CLOSE_PRE, CLOSE_POST, retries=1)


def var_or(*pairs):
    return {"logic": "or", "conditions": [{"variable": v, "operator": "equals", "operand": o} for v, o in pairs]}


def ok_true(tool):
    return var_or((tool + "_response.data.ok", "true"), (tool + "_response.data.ok", "True"))


def transfer_transitions():
    out = []
    for q in QUEUE_REASON:
        conds = [{"variable": "transfer_queue", "operator": "equals", "operand": q}]
        for arg in ("transfer_to_agent_args.queue", "transfer_to_agent_args.body.queue"):
            for tv in ("true", "True"):
                conds.append({"logic": "and", "conditions": [
                    {"variable": "transfer_to_agent_response.data.ok", "operator": "equals", "operand": tv},
                    {"variable": arg, "operator": "equals", "operand": q}]})
        out.append({"target_node": "close_transfer_" + q, "description": "The call was transferred to the " + q + " queue", "condition": {"logic": "or", "conditions": conds}})
    return out


CLOSED = {"target_node": "end_bye", "description": "close_call succeeded",
          "condition": var_or(("close_state", "closed"), ("close_call_response.data.ok", "true"), ("close_call_response.data.ok", "True"))}


def closer(key, text, outcome, reason=None):
    body = {"customer_id": {"type": "string", "default": "{{customer_id}}"}, "outcome": {"type": "string", "default": outcome}}
    if reason: body["reason"] = {"type": "string", "default": reason}
    return {"name": key, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "end_silent",
            "on_enter": {"type": "tts", "text": text}, "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "config": {"timeoutMs": 12000, "maxRetries": 2},
                             "function": {"name": "close_call", "handler": "__function__:api_call", "description": "End the call.",
                                          "parameters": {"type": "object", "properties": {
                                              "url": {"type": "string", "default": BASE + "close_call"},
                                              "method": {"type": "string", "default": "POST"},
                                              "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                                              "body": {"type": "object", "properties": body, "required": []}}}}}],
            "transitions": [{"target_node": "end_silent", "description": "always", "condition": {"operator": "always"}}]}


def end_node(key, fname, message=None):
    props = {"message_mode": {"type": "string", "default": "custom" if message else "none"}}
    if message: props["spoken_message"] = {"type": "string", "default": message}
    return {"name": key, "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
            "terminal": {"type": "function", "function": {"name": fname, "handler": "__function__:end_call", "parameters": {"type": "object", "properties": props}}}}


def code_node(key, code, transitions):
    return {"name": key, "node_type": "code", "passthrough": True, "passthrough_default_transition": transitions[-1]["target_node"],
            "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                                                              "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": code}, "timeout_ms": {"type": "number", "default": 10000}}}}}],
            "transitions": transitions}


# ---------------------------------------------------------------- prompts
SYSTEM = """You are Freya Bank's digital assistant on an outbound phone call. Speak English. Keep every turn to one or two short sentences and ask one question at a time. Address the customer as {{title}} {{last_name}}.

Rules that hold in the whole call:
- When a rule says to use a tool, CALL the tool. Saying that something is done is not enough. Call one tool per message.
- After every tool result, read its what_to_do_next line and do exactly that. Never end a turn with nothing: either speak or call a tool.
- Never say that something is recorded, scheduled or transferred unless the tool result says ok true.
- To end the call: thank the person, say goodbye and call close_call in that same turn. close_call is called exactly once and is the last tool call. If the person answers after your goodbye and the call is not closed yet, call close_call at once without more talk.
- If asked whether you are a robot or an AI: say "I am Freya Bank's digital assistant." and continue where you were.
- If asked whether this is a scam: say they can verify the call by phoning Freya Bank's customer service number, and continue where you were.
- A question you cannot answer from the facts you were given: say "I do not have that information." Never invent figures, dates or reasons.
- Never offer a discount, a waiver, an instalment plan or any other term.
- The customer may give a number or a date in two parts, correct themselves, or talk about other things. Use the corrected, complete value. Things unrelated to the call (for example a wedding date) are not payment dates.
- Write only the words you say. No stage directions."""

CONTACT = """STAGE: opening and security check. Nobody is verified yet.
You already said the opening sentence and asked "Am I speaking with {{title}} {{last_name}}?".

FORBIDDEN in this stage, to everyone: saying why you are calling, and the words collections, debt, payment, overdue, late, loan, card, account balance, or any product or amount. If asked what the call is about, say only that you can explain after the security check (to the customer), or that you can only speak with {{title}} {{last_name}} (to anyone else).

STEP 1: who is on the line
- Only "yes", "that's me", "speaking" or the person saying the customer's own name counts as confirmation. Then go to STEP 2.
- "Hello", "go ahead", "who is this?" or any question is NOT a confirmation. Answer a question first, in one sentence, then ask once more "Am I speaking with {{title}} {{last_name}}?". That is the second ask. If it is still not a clear confirmation after the second ask: thank them, say goodbye and call close_call with outcome not_reached, reason unclear.
- Someone else answers (a relative, a colleague, anyone who is not the customer): do not say why you are calling, ask no security question, tell nothing about the account even if they offer to pay. Ask only when {{title}} {{last_name}} can be reached, then use the CALLBACK rules.
- The person says this number does not belong to the customer (wrong number): thank them, say goodbye and call close_call with outcome not_reached, reason wrong_number.
- The person says the customer has died: say "I am sorry for your loss.", ask nothing else, and call close_call with outcome not_reached, reason deceased.

CALLBACK rules (only when someone else answered)
{{callback_facts}}
- A day and a time inside an ALLOWED SLOT: call schedule_callback with the date as YYYY-MM-DD and the time as HH:MM. "Around eleven in the morning" is 11:00. If you have the day but no clock time, ask for the time.
- A day or a time OUTSIDE the window: say when callbacks are possible and ask once for another time. If the second one is also outside, do not schedule: thank them, say goodbye and call close_call with outcome not_reached, reason no_callback.

STEP 2: two security questions, only with the customer in person, one at a time
- First ask for the date of birth. When the customer answers, call verify_identity with field date_of_birth and the answer as YYYY-MM-DD, whatever way they said it. A date given only as numbers is day, then month, then year.
- When the date of birth has passed, ask for the last four digits of the phone number on file and call verify_identity with field phone_last4.
- Call verify_identity for every answer. The tool result tells you whether it was right and what to do next. Never state yourself that an answer is right or wrong before the result.
- If the customer gives only part of a date or a number, wait for the rest before you call the tool.
- If the customer does not want to answer, or asks what the call is about instead of answering: explain once that the questions protect their account and ask the question again. At the second refusal or the second such question: thank them, say goodbye and call close_call with outcome not_verified, reason declined.
- If the customer says for the second time that they do not want to talk: thank them, say goodbye and call close_call with outcome not_verified, reason declined.
- If the customer asks for a human: ask once whether you can help first. If they insist, call transfer_to_agent with queue general."""

COLLECT = """STAGE: getting a payment promise. The customer is verified. You already said you are calling from the collections team about a late payment, named the product, the past-due amount and the days late, and asked "Can you make this payment today?" for the FULL amount.

FACTS from the bank. Never calculate a date or an amount yourself, look it up here:
{{account_facts}}

You track AMOUNT (it starts as FULL) and ATTEMPTS USED (it starts at 0). You propose every amount: never ask "how much can you pay?". When you say a date, say the weekday, the day and the month.

FIRST check these special situations:
A. The customer mentions illness, a death in the family or job loss: say one short sentence of sympathy, make NO persuasion attempt, and offer to connect them with the customer support team. If they accept, call transfer_to_agent with queue support. If they decline: thank them, say goodbye, call close_call with outcome no_promise, reason hardship. Only if they themselves still name a day or an amount, handle that as a normal promise. A comes before every other rule.
B. "I already paid" this amount: say "Payments can take one business day to appear." Take no promise. Thank them, say goodbye, call close_call with outcome paid.
C. "This debt is not mine", "I never had this product", or "it was closed / paid off long ago": do not argue, take no promise. Thank them, say goodbye, call close_call with outcome no_promise, reason dispute.
D. The customer asks for a new payment plan or lower instalments: do not describe any plan. Call transfer_to_agent with queue restructuring.
E. The customer asks for a human: ask once whether you can help first. If they insist, call transfer_to_agent with queue general.
F. The customer says for the second time that they do not want to talk: thank them, say goodbye, call close_call with outcome no_promise, reason refused.
G. The customer asks for a discount or a waiver: say you cannot offer that, and continue.

THEN the normal handling:
1. Yes to paying today: the date is TODAY. Go to CONFIRM.
2. The customer names a day. This is never a refusal. Find the day in CALENDAR or WEEKDAY WORDS:
   - OK: use it. Go to CONFIRM.
   - NOT ALLOWED (weekend or bank holiday): say that day cannot be recorded and offer the next business day written there. If they accept, go to CONFIRM.
   - AFTER THE DEADLINE: say the latest date you can record is the DEADLINE and offer it. If they accept, go to CONFIRM. If they refuse the DEADLINE, that is a flat refusal (rule 6).
3. The customer gives a vague period and no exact day ("next week", "the week after next", "end of the month", "when my salary comes"): ask once for the exact day. If they still give no exact day, propose the DEADLINE.
4. The customer says the full amount is too much and names no amount: offer the FLOOR, once, and say that the rest stays overdue. AMOUNT becomes FLOOR. If they refuse the FLOOR, that is a flat refusal (rule 6).
5. The customer names an amount. This is never a refusal. From FLOOR up to FULL, both included: accept it, AMOUNT becomes that amount, and ask whether they can pay it today if no day was given. Below FLOOR: say it cannot be recorded and offer the FLOOR once (the rest stays overdue). If they do not accept the FLOOR, that is a flat refusal (rule 6).
6. FLAT REFUSAL: a no or a non-answer with no day and no amount of their own (for example "No, I cannot pay today"). Each flat refusal uses one persuasion attempt:
   - ATTEMPTS USED 0: make Attempt 1. Propose paying AMOUNT (FULL, or FLOOR if they already said the full amount is too much) on the ATTEMPT 1 DATE. ATTEMPTS USED becomes 1.
   - ATTEMPTS USED 1 and ATTEMPT 2 exists: make Attempt 2. Propose the FLOOR on the DEADLINE. ATTEMPTS USED becomes 2.
   - No attempt left: stop. Do not repeat an offer they already refused. Thank them, say goodbye, call close_call with outcome no_promise, reason refused.
   If the customer answers an attempt with a day or an amount of their own, use rules 2 to 5. If they accept, go to CONFIRM.

CONFIRM: read back the product, the amount and the date in one sentence and ask "Do you confirm?". Example: "You will pay 420.00 for your credit card ending 4821 on Friday 16 October. Do you confirm?" Only after a clear yes, call create_promise with that amount and that date as YYYY-MM-DD. If the customer changes the day or the amount now, check the new value with rules 2 to 5 and read it back again.
After create_promise, follow what_to_do_next in the result. Say the promise is recorded only when the result says ok true. If the service failed, call create_promise once more. If it fails a second time, say you could not record the promise, thank them, say goodbye and call close_call with outcome no_promise, reason service_error."""

AFTER = """STAGE: the promise is recorded. You already told the customer that it is recorded and that a text message will confirm it, and asked whether there is anything else.
- A recorded promise cannot be changed or cancelled in this call. If the customer asks for that, say they can change it in the mobile app. Do not record anything else.
- If the customer asks for a new payment plan or lower instalments: do not describe a plan, call transfer_to_agent with queue restructuring.
- If the customer asks for a human: ask once whether you can help first. If they insist, call transfer_to_agent with queue general.
- If the customer mentions illness, a death in the family or job loss: one short sentence of sympathy and offer the customer support team. If they accept, call transfer_to_agent with queue support.
- When the customer needs nothing else: thank them, say goodbye and call close_call with outcome promise."""

BYE = "Thank you for your time. Goodbye."
nodes = {}
nodes["setup"] = code_node("setup", SETUP, [{"target_node": "contact", "description": "always", "condition": {"operator": "always"}}])
nodes["contact"] = {
    "name": "contact", "node_type": "conversation",
    "on_enter": {"type": "tts", "text": "Hello, this is Freya Bank's digital assistant calling, and we are recording this call. Am I speaking with {{title}} {{last_name}}?"},
    "on_enter_first_entry_only": True,
    "task_message": CONTACT,
    "tools": [verify_tool(), callback_tool(), transfer_tool(["general"]),
              close_tool(["not_reached", "not_verified", "transferred"], ["unclear", "wrong_number", "deceased", "no_callback", "callback", "declined", "failed", "human"])],
    "max_node_turns": 40,
    "transitions": [
        {"target_node": "get_account", "description": "Both security answers are verified",
         "condition": var_or(("verify_identity_response.data.all_verified", "true"), ("verify_identity_response.data.all_verified", "True"))},
        {"target_node": "close_failed", "description": "Three wrong answers",
         "condition": var_or(("id_state", "failed"), ("verify_identity_response.data.wrong_answers_left", "0"))},
        {"target_node": "close_callback", "description": "The callback is scheduled", "condition": ok_true("schedule_callback")},
        *transfer_transitions(), CLOSED],
}
nodes["get_account"] = {
    "name": "get_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "compute",
    "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
    "pre_actions": [api_tool("get_account", "Get the late product of the customer.", {}, [], post=GET_ACCOUNT_POST, retries=2)],
    "response_mapping": [{"path": "data.products", "variable": "products"}, {"path": "data.pf_json", "variable": "pf_json"}],
    "transitions": [{"target_node": "compute", "description": "always", "condition": {"operator": "always"}}],
}
nodes["compute"] = code_node("compute", COMPUTE, [
    {"target_node": "collect", "description": "Account facts are ready", "condition": var_or(("account_state", "ok"))},
    {"target_node": "close_no_account", "description": "always", "condition": {"operator": "always"}}])
nodes["collect"] = {
    "name": "collect", "node_type": "conversation",
    "on_enter": {"type": "tts", "text": "Thank you, your identity is verified. I am calling from the collections team of Freya Bank about a late payment on your {{product_words}}. The past-due amount is {{full_text}} and it is {{days_text}} late. Can you make this payment today?"},
    "on_enter_first_entry_only": True,
    "task_message": COLLECT,
    "tools": [promise_tool(), transfer_tool(["restructuring", "general", "support"]),
              close_tool(["paid", "no_promise", "transferred", "promise"], ["refused", "hardship", "dispute", "service_error", "restructuring", "human", "support"])],
    "max_node_turns": 60,
    "transitions": [
        {"target_node": "after_promise", "description": "The promise is recorded",
         "condition": {"logic": "or", "conditions": [
             {"variable": "promise_state", "operator": "equals", "operand": "recorded"},
             {"variable": "create_promise_response.data.ok", "operator": "equals", "operand": "true"},
             {"variable": "create_promise_response.data.ok", "operator": "equals", "operand": "True"}]}},
        {"target_node": "close_service_error", "description": "create_promise failed twice",
         "condition": {"logic": "or", "conditions": [{"variable": "cp_fail", "operator": "greater_than", "operand": 1}]}},
        *transfer_transitions(), CLOSED],
}
nodes["after_promise"] = {
    "name": "after_promise", "node_type": "conversation",
    "on_enter": {"type": "tts", "text": "Your promise is recorded. You will receive a text message confirming this promise. Is there anything else I can help you with?"},
    "on_enter_first_entry_only": True,
    "task_message": AFTER,
    "tools": [transfer_tool(["restructuring", "general", "support"]), close_tool(["promise", "transferred"], ["restructuring", "human", "support"])],
    "extraction_schema": [{"name": "wrap_up", "type": "string", "enum_values": ["nothing_more", "something_else"], "min_confidence": 0.5,
                           "description": "Look only at the customer's LAST message. nothing_more: the customer says no, nothing else, that is all, okay, thanks or goodbye. something_else: the customer asks a question or asks for something (a change, a cancellation, a plan, a person)."}],
    "max_node_turns": 4, "max_turn_transition": "close_promise",
    "transitions": [
        *transfer_transitions(), CLOSED,
        {"target_node": "close_promise", "description": "The customer needs nothing else", "condition": var_or(("wrap_up", "nothing_more"))}],
}
nodes["close_promise"] = closer("close_promise", BYE, "promise")
nodes["close_failed"] = closer("close_failed", "I am sorry, I could not verify your identity, so I cannot continue this call. Please use the Freya Bank mobile app or visit a branch. " + BYE, "not_verified", "failed")
nodes["close_callback"] = closer("close_callback", "Thank you, the callback is scheduled. Goodbye.", "not_reached", "callback")
nodes["close_service_error"] = closer("close_service_error", "I am sorry, I could not record your promise because of a technical problem. Nothing was recorded. " + BYE, "no_promise", "service_error")
nodes["close_no_account"] = closer("close_no_account", "Thank you, your identity is verified. I am sorry, I cannot reach your details right now because of a technical problem. " + BYE, "no_promise", "service_error")
for q, r in QUEUE_REASON.items():
    nodes["close_transfer_" + q] = closer("close_transfer_" + q, "I am connecting you now. " + BYE, "transferred", r)
nodes["end_silent"] = end_node("end_silent", "call_closed")
nodes["end_bye"] = end_node("end_bye", "call_closed_by_agent", "Goodbye.")

workflow = {"initial_node": "setup",
            "system_prompt": SYSTEM,
            "nodes": nodes, "global_nodes": {},
            "test_variables": {"customer_id": "c_test", "first_name": "Daniel", "last_name": "Reyes", "title": "Mr", "now": "2026-10-14T11:00"}}

out = pathlib.Path(__file__).resolve().parent / "brief-a.workflow.json"
out.write_text(json.dumps(workflow, indent=1))
print("wrote", out, len(json.dumps(workflow)), "bytes")

if "--selftest" in sys.argv:
    for prod in ({"product_id": "p1", "type": "credit_card", "card_last4": "4821", "past_due": 420.0, "days_late": 6, "minimum_payment": 126.0},
                 {"product_id": "p1", "type": "car_loan", "past_due": 1250.0, "days_late": 14, "instalment_amount": 400.0}):
        v = {"now": "2026-10-14T11:00"}
        exec(SETUP, {"vars": v}, {})
        v["products"] = json.dumps([prod])
        exec(COMPUTE, {"vars": v}, {})
        print(v["callback_facts"]); print(v["account_state"]); print(v["account_facts"]); print(v["ok_dates"])
    v = {"id_dob_ok": "no"}
    for a in ("14 March 1988", "March 14th, 1988", "14/03/1988", "1988-3-14", "the 2nd of November 1979"):
        c = {"body": {"field": "date_of_birth", "answer": a}}
        exec(VERIFY_PRE, {"vars": v, "config": c}, {}); print(a, "->", c["body"])
    c = {"url": "x", "body": {"date": "2026-10-15", "time": "11"}}
    exec(CALLBACK_PRE, {"vars": {"cb_today": "2026-10-14", "cb_next": "2026-10-15", "cb_today_ok": "yes", "cb_now_hm": "11:00"}, "config": c}, {}); print(c)
    c = {"url": "x", "body": {"date": "2026-10-26", "amount": "540", "product_id": "zz"}}
    vv = {"product_id": "p1", "ok_dates": "2026-10-27", "floor_amount": "180.0", "full_amount": "540.0"}
    exec(PROMISE_PRE, {"vars": vv, "config": c}, {}); print(c, vv)
    c = {"body": {"outcome": "no_promise", "reason": "refused"}}
    exec(CLOSE_PRE, {"vars": {"promise_state": "recorded", "transfer_queue": "none"}, "config": c}, {}); print(c)
    r = {"data": None, "header": {}}
    exec(PROMISE_POST, {"vars": {"cp_fail": 0}, "result": r}, {}); print(r)
