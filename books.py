"""Local lexical book retrieval; citations are verified against provided passages."""
import json
import re

STOP=set('the a an and of to in is for with which what explain identify describe this that from how on as are by or it its at be'.split())

def initialize(db):
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS book_chunks USING fts5(book_id UNINDEXED,page UNINDEXED,text, tokenize='unicode61')")

def index(db,book_id,pages):
    db.execute('DELETE FROM book_chunks WHERE book_id=?',(book_id,))
    for page,text in enumerate(pages,1):
        text=re.sub(r'\s+',' ',text).strip()
        for start in range(0,len(text),850):
            chunk=text[start:start+1100]
            if len(chunk)>=40:db.execute('INSERT INTO book_chunks(book_id,page,text) VALUES (?,?,?)',(book_id,page,chunk))

def retrieve(db,query,limit=3,kind="book",document_id=None):
    terms=list(dict.fromkeys(t for t in re.findall(r'[^\W_]+',query.lower()) if len(t)>2 and t not in STOP))[:40]
    if not terms:return []
    match=' OR '.join('"'+t+'"' for t in terms)
    rows=db.execute('SELECT c.rowid,c.book_id,c.page,c.text,p.name FROM book_chunks c JOIN papers p ON p.id=c.book_id WHERE book_chunks MATCH ? AND p.kind=? AND (? IS NULL OR p.id=?) ORDER BY bm25(book_chunks) LIMIT ?', (match,kind,document_id,document_id,limit)).fetchall()
    return [{'id':str(r['rowid']),'book_id':r['book_id'],'book':r['name'],'page':int(r['page']),'excerpt':r['text'][:700]} for r in rows]

def validate_citations(citations,passages):
    allowed={p['id']:p for p in passages};verified=[]
    for citation in citations:
        source=allowed.get(citation['source_id']);quote=citation.get('quote','').strip()
        if source and not quote:quote=source['excerpt']
        if not source or len(quote)<15 or ' '.join(quote.split()).casefold() not in ' '.join(source['excerpt'].split()).casefold():
            raise ValueError('The model supplied a citation that does not match a retrieved book passage. Try a more specific topic or a different book section.')
        verified.append({**source,'quote':quote})
    if not verified:raise ValueError('The model did not support this answer with a book citation.')
    return verified


def syllabus_sources(db,syllabus_id,query):
    if not syllabus_id:return []
    if not db.execute("SELECT 1 FROM papers WHERE id=? AND kind='syllabus'",(syllabus_id,)).fetchone():raise ValueError('Select an uploaded syllabus or leave the syllabus unset.')
    sources=retrieve(db,query,limit=2,kind='syllabus',document_id=syllabus_id)
    if not sources:raise ValueError('No matching syllabus objectives found for this topic. Choose another exam example or leave the syllabus unset.')
    return sources

def validate_syllabus(question,sources):
    if not sources:return
    source_id=question.pop('syllabus_source_id','')
    if source_id:
        source=next((s for s in sources if s['id']==source_id),None)
        if not source:raise ValueError('The model selected an unknown syllabus objective.')
        question.pop('syllabus_quote',None);question['syllabus_source']={**source,'quote':source['excerpt']};return
    quote=question.pop('syllabus_quote','').strip()
    if len(quote)<10:raise ValueError('The model did not identify a syllabus objective for this question.')
    source=next((s for s in sources if ' '.join(quote.split()).casefold() in ' '.join(s['excerpt'].split()).casefold()),None)
    if not source:raise ValueError('The model supplied an unverified syllabus objective. This question was omitted.')
    question['syllabus_source']={**source,'quote':quote}
