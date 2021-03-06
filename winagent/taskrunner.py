import asyncio
import json
import subprocess
from time import perf_counter

import requests

from agent import WindowsAgent


class TaskRunner(WindowsAgent):
    def __init__(self, task_pk, log_level, log_to):
        super().__init__(log_level, log_to)
        self.task_pk = task_pk
        self.task_url = f"{self.astor.server}/api/v1/{self.task_pk}/taskrunner/"

    def run(self):
        # called manually and not from within a check
        ret = self.get_task()
        if not ret:
            return False

        asyncio.run(self.run_task(ret))

    async def run_while_in_event_loop(self):
        # called from inside a check
        ret = self.get_task()
        if not ret:
            return False

        await asyncio.gather(self.run_task(ret))

    def get_task(self):
        try:
            resp = requests.get(self.task_url, headers=self.headers, timeout=15)
        except Exception as e:
            self.logger.debug(e)
            return False
        else:
            return resp.json()

    async def run_task(self, data):

        try:
            script = data["script"]
            timeout = data["timeout"]
        except Exception as e:
            self.logger.debug(e)
            return False

        try:
            if script["shell"] == "python":
                cmd = [
                    self.salt_call,
                    "win_agent.run_python_script",
                    script["filename"],
                    f"timeout={timeout}",
                ]

                try:
                    script_type = script["script_type"]
                except KeyError:
                    pass
                else:
                    cmd.append(f"script_type={script_type}")
            else:
                cmd = [
                    self.salt_call,
                    "cmd.script",
                    script["filepath"],
                    f"shell={script['shell']}",
                    f"timeout={timeout}",
                ]

            self.logger.debug(cmd)
            start = perf_counter()

            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            proc_timeout = int(timeout) + 2

            try:
                proc_stdout, proc_stderr = await asyncio.wait_for(
                    proc.communicate(), proc_timeout
                )
            except asyncio.TimeoutError:
                try:
                    proc.terminate()
                except:
                    pass

                self.logger.debug(f"Task timed out after {timeout} seconds")
                proc_stdout, proc_stderr = False, False
                stdout = ""
                stderr = f"Task timed out after {timeout} seconds"
                retcode = 98

            stop = perf_counter()

            if proc_stdout:
                resp = json.loads(proc_stdout.decode("utf-8", errors="ignore"))
                retcode = resp["local"]["retcode"]
                stdout = resp["local"]["stdout"]
                stderr = resp["local"]["stderr"]

            elif proc_stderr:
                retcode = 99
                stdout = ""
                stderr = proc_stderr.decode("utf-8", errors="ignore")

            payload = {
                "stdout": stdout,
                "stderr": stderr,
                "retcode": retcode,
                "execution_time": "{:.4f}".format(round(stop - start)),
            }
            self.logger.debug(payload)

            resp = requests.patch(
                self.task_url, json.dumps(payload), headers=self.headers, timeout=15,
            )

        except Exception as e:
            self.logger.debug(e)

        return "ok"
