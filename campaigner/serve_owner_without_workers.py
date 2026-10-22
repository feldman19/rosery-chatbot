"""Serve owner UI without starting background collection or execution workers."""
from server import make_server

if __name__ == '__main__':
    from business_context import load_rosery_dossier
    print('Business profile: '+str(load_rosery_dossier().get('profile_source')),flush=True)
    make_server(8767, workspace='manager', start_live=False).serve_forever()
