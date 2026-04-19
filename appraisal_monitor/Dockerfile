FROM python:3.11-slim

RUN pip3 install requests

COPY appraisal_monitor.py /appraisal_monitor.py
COPY run.sh /run.sh

RUN chmod +x /run.sh

CMD ["/run.sh"]
