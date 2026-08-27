"""Isolated browser QA: real application UI/HTTP with a synthetic speech provider.

This helper never opens a microphone or the workstation database/configuration.
Run from the repo: python test/manual_speech_server.py --scenario success
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bagu


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("success", "unavailable", "denied", "network", "timeout"), default="success")
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    shim = "const speechQaScenario = " + json.dumps(args.scenario) + ";" + r"""
    // QA only: no getUserMedia, real recognizer, or remote audio request.
    document.title = '语音输入 QA（模拟服务）';
    window.webkitSpeechRecognition = undefined;
    window.SpeechRecognition = speechQaScenario === 'unavailable' ? undefined : class {
      start() {
        if (speechQaScenario === 'timeout') return;
        this.timer = setTimeout(() => {
          if (speechQaScenario === 'denied' || speechQaScenario === 'network') {
            if (this.onerror) this.onerror({error: speechQaScenario === 'denied' ? 'not-allowed' : 'network'});
            return;
          }
          if (this.onstart) this.onstart();
          if (this.onresult) this.onresult({resultIndex:0, results:[Object.assign([{transcript:'原子性、一致性'}], {isFinal:false})]});
        }, 150);
      }
      stop() {
        clearTimeout(this.timer);
        this.timer = setTimeout(() => {
          if (this.onresult) this.onresult({resultIndex:0, results:[Object.assign([{transcript:'原子性、一致性、隔离性和持久性。'}], {isFinal:true})]});
          if (this.onend) this.onend();
        }, 150);
      }
      abort() { clearTimeout(this.timer); if (this.onend) this.onend(); }
    };
    """
    with tempfile.TemporaryDirectory(prefix="bagu-speech-qa-") as tmp:
        root = Path(tmp)
        db = root / "qa.db"
        conn = bagu.get_conn(db)
        try:
            bagu.init_db(conn)
            bagu.create_question(conn, {
                "category": "语音验收", "question": "请说明数据库事务的特性",
                "answer": "原子性、一致性、隔离性、持久性", "url": "",
            })
            bagu.draw(conn, 1)
        finally:
            conn.close()
        base = bagu.make_http_handler(root=root, db_path=db, static_root=ROOT)

        class Handler(base):
            def _write(self, code, payload, ctype):
                if ctype.startswith("text/html"):
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    payload = payload.replace("<script>", "<script>" + shim + "</script><script>", 1)
                super()._write(code, payload, ctype)

        server = bagu.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"Synthetic speech QA ({args.scenario}): http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
