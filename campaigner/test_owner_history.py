"""History projection uses recorded events only; no network or runtime state."""
import subprocess
import unittest
from pathlib import Path

PAGE = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
HELPERS = PAGE.split('function historyDay(', 1)[1].split('function renderActed()', 1)[0]
RESULT = PAGE.split('function resultLine(', 1)[1].split('function factPart', 1)[0]


class OwnerHistoryTests(unittest.TestCase):
    def test_grouping_labels_counts_and_sources(self):
        script = "const assert=require('assert');const ACT_STATE={blocked:['idle','לא נשלח']};function resultLine(" + RESULT + 'function historyDay(' + HELPERS + r"""
const now=new Date('2026-10-24T12:00:00Z');
assert.equal(historyDayLabel('2026-10-24',now),'היום');
assert.equal(historyDayLabel('2026-10-23',now),'אתמול');
assert.equal(historyDayLabel(null,now),'תאריך לא ידוע');
assert.equal(historyDay('2026-10-23T22:30:00Z'),'2026-10-24');
assert.equal(historyTodayLine([],now),'');
assert.equal(resultLine({phase:'final',lines:[]}),null);
assert.equal(historyTitle('pause','AFTER HOUR C14'),'עצרת את המודעה AFTER HOUR C14');
assert.equal(historyTitle('drift','0851'),'כיבית שינויים אוטומטיים של מטא במודעה 0851');
const receipts=[{id:'1',target:'pause:1',state:'verified',approved_at:'2026-10-24T10:00:00Z'},
 {id:'2',target:'drift:2',state:'verified',approved_at:'2026-10-23T09:00:00Z'},
 {id:'3',target:'pause:3',state:'blocked',approved_at:'2026-10-24T11:00:00Z'}];
const entries=historyEntries(receipts,[{id:'m',proposal_id:'scale:4',kind:'scale',source:'owner_marked',change_day:'2026-10-22',verification:'not_checked'},
 {id:'d',proposal_id:'pause:5',kind:'pause',source:'auto_detected'}],
 [{proposal_id:'p',dismissed_at:'2026-10-24T12:00:00Z',name:'המלצה'}],
 [{id:'e',plan:{state:'interrupted',question:'בדיקה'},verdict:'הניסוי הופסק'},
 {id:'running',plan:{state:'running'}}]);
assert.equal(entries.length,7);
assert.equal(entries[0].type,'dismiss');
assert.equal(entries.at(-1).type,'experiment');
assert.equal(entries.at(-1).at,null);
assert.equal(historyTodayLine(entries,now),'השבוע בוצעו 2 פעולות · היסטוריה ›');
assert.equal(historyWeek(entries,now).length,5);
assert(entries.find(e=>e.id==='mark:m').who.includes('ביצעתי'));
assert(entries.find(e=>e.id==='mark:d').who.includes('זוהה במטא'));
assert(entries.find(e=>e.id==='receipt:3').status.includes('לא נשלח'));
assert.equal(historyTodayLine([{type:'meta',done:true,at:'2026-10-25'}],now),'');
const merged=historyEntries(receipts,[{id:'duplicate',proposal_id:'pause:1',source:'auto_detected',change_day:'2026-10-24'}],[],[]);
assert.equal(merged.length,3);
assert(merged.find(e=>e.id==='receipt:1').who.includes('זוהה במטא'));
assert.equal(historyTodayLine(merged,now),'השבוע בוצעו 2 פעולות · היסטוריה ›');
"""
        done = subprocess.run(['node', '-e', script], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_history_navigation_filters_and_undo_confirmation(self):
        for element in ('menu-history', 'history-back', 'history-summary', 'history-filters'):
            self.assertIn('id="' + element + '"', PAGE)
        for label in ('הכל', 'פעולות במטא', 'הסתרות', 'ניסויים'):
            self.assertIn(label, PAGE)
        self.assertIn("openAction('undo:'+r.id)", PAGE)
        self.assertIn('אחרי הצגת השינוי ואישור נוסף', PAGE)
        self.assertIn("go(historyReturn)", PAGE)
