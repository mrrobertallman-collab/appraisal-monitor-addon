#!/usr/bin/with-contenv bashio

GMAIL_ACCOUNT_1=$(bashio::config 'gmail_account_1')
APP_PASSWORD_1=$(bashio::config 'app_password_1')
GMAIL_ACCOUNT_2=$(bashio::config 'gmail_account_2')
APP_PASSWORD_2=$(bashio::config 'app_password_2')
HA_TOKEN=$(bashio::config 'ha_token')
POLL_INTERVAL=$(bashio::config 'poll_interval')
LOG_LEVEL=$(bashio::config 'log_level')

bashio::log.info "Starting Appraisal Monitor..."
bashio::log.info "Account 1: ${GMAIL_ACCOUNT_1}"
bashio::log.info "Account 2: ${GMAIL_ACCOUNT_2}"
bashio::log.info "Poll interval: ${POLL_INTERVAL}s"

export GMAIL_ACCOUNT_1
export APP_PASSWORD_1
export GMAIL_ACCOUNT_2
export APP_PASSWORD_2
export HA_TOKEN
export POLL_INTERVAL
export LOG_LEVEL

exec python3 /appraisal_monitor.py
