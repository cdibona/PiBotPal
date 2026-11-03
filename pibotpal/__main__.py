import uvicorn

if __name__ == '__main__':
    uvicorn.run('pibotpal.app:create_app', factory=True, host='127.0.0.1', port=8765,
                log_level='info', access_log=False, proxy_headers=False)
