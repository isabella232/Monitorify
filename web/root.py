import cherrypy, os, api
from cherrypy.lib.static import serve_file

class Controller:
    """ Primary handler class """

    def __init__(self, config, db):
        # Open the configuration file
        self.config = config
        self.db = db

        # Map in classes to sub-URLs
        self.api = api.Controller(self.config, self.db);

    @cherrypy.expose
    def default(self, x1=None, x2=None, x3=None, x4=None, x5=None, x6=None, x7=None):
        return serve_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html'), content_type='text/html')

