"""Apply current screening rules to an already captured live run, without network access."""
import argparse
import gzip
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from collector_state import State
from indonesia import BATAM, BANK, batam, bank, save
from attachments import Documents
from contract_export import export_collector_runs


def reprocess(captured_run, captured_state, robots_file, output, target_state):
    report = json.loads((captured_run / "last_run.json").read_text())
    observed = datetime.fromisoformat(report["observed_at"].replace("Z","+00:00"))
    original = State(captured_state)
    state = State(target_state)
    for table in ("raw","pdf","pdf_versions","extracted"):
        rows = original.db.execute(f"SELECT * FROM {table}").fetchall()
        if rows:
            with state.db:
                state.db.executemany(f"INSERT OR IGNORE INTO {table} VALUES ({','.join('?' for _ in rows[0])})",rows)
    snapshots = {}
    for source,key,_,blob in original.db.execute("SELECT source,key,observed,body FROM raw ORDER BY observed"):
        snapshots[(source,key)] = gzip.decompress(blob)
    robots = robots_file.read_bytes()
    if b"User-agent" not in robots and b"user-agent" not in robots:
        raise ValueError("Original robots policy is required for replay")

    def respond(request):
        url = str(request.url)
        if url == "https://www.plnbatam.com/robots.txt":
            return httpx.Response(200,content=robots)
        if url == BATAM:
            return httpx.Response(200,content=snapshots[("pln_batam",BATAM)])
        if str(request.url).startswith(BANK):
            offset = request.url.params.get("os","0")
            return httpx.Response(200,content=snapshots[("world_bank",offset)])
        pdf = original.pdf(url)
        if pdf:
            return httpx.Response(304) if "if-none-match" in request.headers or "if-modified-since" in request.headers else httpx.Response(200,content=pdf[2])
        # Recorded documents that were never downloaded remain unavailable.
        return httpx.Response(503, text="Attachment bytes were not captured")

    output.mkdir(parents=True,exist_ok=True)
    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            cards, sources = {}, []
            for source, fetch in (
                    ("pln_batam",lambda:batam(client,observed,True,state)),
                    ("world_bank",lambda:bank(client,observed,15,state))):
                records, info = fetch()
                downloader = Documents(client,state,output/"documents")
                for record in records:
                    record.documents = [downloader.fetch(source,record.source_tender_id,doc) for doc in record.documents]
                changes = state.merge(source,records,observed)
                cards[source] = records
                info.update(changes=changes, verification_mode="reprocessed_saved_live_capture",
                            with_official_url=sum(bool(r.tender_url) for r in records),
                            with_contacts=sum(bool(r.contacts) for r in records),
                            with_documents=sum(bool(r.documents) for r in records),
                            with_downloaded_documents=sum(any(d.get("download_status")=="downloaded" for d in r.documents) for r in records))
                sources.append(info)
            sources.extend(src for src in report["sources"] if src["source_id"] not in cards)
            categories = {"active_tenders":[],"pending_verification":[],"tender_history":[]}
            for records in cards.values():
                for record in records:
                    key = "tender_history" if record.entry_status=="closed" else "pending_verification"
                    categories[key].append(record.model_dump(mode="json"))
            for key,records in categories.items():
                save(output/(key+".json"),records)
            summary = dict(report,sources=sources,
                           processing_mode="reprocessed_saved_live_capture",
                           processing_note="Counts recomputed using current candidate rules on captured official bytes; no network requests",
                           counts={k.replace("_"," ").title():len(v) for k,v in categories.items()})
            save(output/"last_run.json",summary)
            save(output/"sources_monitoring.json",sources)
            for source in sources:
                state.source_run(source["source_id"],observed.isoformat(),source)
            save(output/"stored_tenders.json",state.all())
            export_collector_runs(output)
            print(json.dumps({"counts":summary["counts"],"sources":[{k:s.get(k) for k in (
                "source_id","records_loaded","candidates","with_contacts","with_documents","with_downloaded_documents")} for s in sources]},ensure_ascii=False))
    finally:
        state.close()
        original.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--captured-run",type=Path,required=True)
    parser.add_argument("--captured-state",type=Path,required=True)
    parser.add_argument("--robots-file",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--target-state",type=Path,required=True)
    args=parser.parse_args()
    reprocess(args.captured_run,args.captured_state,args.robots_file,args.output,args.target_state)
