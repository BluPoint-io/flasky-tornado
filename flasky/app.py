# -*- coding: utf-8 -*-
"""
    Flasky.app module implements the tornado.web.Application wrapper
"""

import functools
from asyncio import iscoroutinefunction
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from tornado.web import Application, StaticFileHandler
from tornado.ioloop import IOLoop, PeriodicCallback

from .errors import ConfigurationError, default_error_handler_func
from .handler import DynamicHandler
from .di import DIContainer
from .cache import CacheManager
from .helpers import _HandlerBoundObject
from .parameters import ParameterResolver


class FlaskyApp(object):
    """The FlaskyApp object provides a convenient API to configure
    tornado.Application. It acts as a container for all configuration
    functions, objects and settings. On application bootstrap FlaskyApp instance
    registers DynamicHandler classes for every endpoint that is  defined by
    user of library.

    A FlaskyApp object should be created in main module or :file:`__init__.py::

        from flasky.app import FlaskyApp
        app = FlaskyApp(**settings)

    :param ioloop: IOLoop that will be used by application.
    :param settings: Application specific settings.

    """

    #: The class for used as object container
    #: See :class:`~flasky.DIContainer`
    di_class = DIContainer

    #: The class for used as parameter resolver
    #: See :class: `~flasky.parameters.ParameterResolver`
    parameter_resolver_class = ParameterResolver

    #: The class for used as cache manager
    #: See :class: `~flasky.cache.CacheManager`
    cache_manager_class = CacheManager

    #: The debug flag. This flag will be passed to tornado.Application
    #: If the debug flag set True, Application will be reload on every if code
    #: changes detected and Application will spit detailed logging information.
    debug = False

    #: The name of the logger to use.
    logger_name = "flasky.logger"


    def __init__(self, ioloop=None, **settings):
        self.ioloop = ioloop
        if not ioloop:
            self.ioloop = IOLoop.current(instance=False)

        #: List of functions which will be runned before any request will be handled 
        #: This functions might be used for any initialization routine
        self.on_start_funcs = []

        #: A list of functions which will be called at the beginning of the
        #: request. Before request functions can be used to perform common 
        #: cross-cutting concerns (logging, authorization etc.)
        #: To register a function use the :meth:`before_request` decorator
        self.before_request_funcs = []

        #: A list of functions which will be called after the request handled 
        #: by handler function.
        #: 
        #: Warning: This function will not be called if any error occur during
        #: handlers to execute a function after request under any 
        #: circumstances please please check :meth:`teardown_request` 
        #:
        #: To register a function use the :meth:`after_request` decorator
        self.after_request_funcs = []
        
        #: A list of functions which will be called after request is handled
        #: this function will be called even if any error exists on handler
        #: function. This method can :meth:`on_teardown_request`
        self.teardown_request_funcs = []

        #: A error specific handler registry. Key will be type of error and None
        #: type will be used as default error handler. 
        #: To register an error handler, use the :meth:`error_handler`
        #: decorator
        self.error_handlers = {None: default_error_handler_func}
        
        #: A list of functions which will be executed after request no matter
        #: what happens.
        #: To register an teardown_function, use the 
        #: :meth:`on_teardown_request` function
        self.teardown_request_funcs = []

        #: TODO: delete this userloader function
        self.user_loader_func = None

        #: A error specific handler registry. Key will be type of error and None
        #: type will be used as default error handler. 
        #:
        #: To register an error handler, use the :meth:`error_handler`
        #: decorator
        self.error_handlers = {None: default_error_handler_func}

        self.host_definitions = OrderedDict()
        self.endpoints = OrderedDict()




        self.settings = settings
        self.option_files = []
        self.app = None
        self.static_file_handler_definitions = []
        self.periodic_function_definitions = []
        self.periodic_functions = []
        self.cache_definitions = []
        self.caches = {}
        self.executor = ThreadPoolExecutor(max_workers=(settings.get('max_worker_count', None) or 1))
        self.is_builded = False
        self.di = self.di_class(self)
        self.parameter_resolver = self.parameter_resolver_class(self)
        self.cache = self.cache_manager_class(self)


    def api(self, host='.*$', endpoint=None, method=None, **kwargs):
        host_definition = self.host_definitions.get(host, None)
        if host_definition is None:
            host_definition = OrderedDict()
            self.host_definitions[host] = host_definition

        endpoint_definition = self.host_definitions.get(host).get(endpoint, None)
        if endpoint_definition == None:
            endpoint_definition = OrderedDict()
            for supported_method in DynamicHandler.SUPPORTED_METHODS:
                endpoint_definition[supported_method] = OrderedDict()
            host_definition[endpoint] = endpoint_definition

        def decorator(f):
            if not iscoroutinefunction(f):
                raise ConfigurationError(message="Function [{}] should be coroutine in order to use."
                                         .format(f.__name__))
            if not endpoint:
                raise ConfigurationError(message='Endpoint should be provided.')

            if not method:
                raise ConfigurationError(message='Method should be provided')

            if method not in DynamicHandler.SUPPORTED_METHODS:
                raise ConfigurationError(message='Unsuppoterted method {}'.format(method))

            self.host_definitions[host][endpoint][method] = {
                'function': f
            }

            self.host_definitions[host][endpoint][method].update(kwargs)
            return f

        return decorator

    def user_loader(self, f):
        self.user_loader_func = f
        return f

    def before_request(self, f):
        if not f:
            raise ValueError('Function cant be none')
        self.before_request_funcs.append(f)
        return f

    def after_request(self, f):
        self.after_request_funcs.append(f)
        return f

    def teardown_request(self, f):
        self.teardown_request_funcs.append(f)
        return f

    def serve_static_file(self, pattern, path):
        if not pattern:
            raise ValueError('Pattern should be specified...')

        if path == None:
            raise ValueError('Path should be specified.')

        self.static_file_handler_definitions.append((pattern, {
            'path': path
        }))

    def build_app_ctx(self):
        return ApplicationContext()

    def build_app(self, host="0.0.0.0"):
        self.app = Application(default_host=host, **self.settings)
        app_ctx = self.build_app_ctx()

        for host, host_definition in self.host_definitions.items():
            for endpoint, endpoint_definition in host_definition.items():
                handler = self._create_dynamic_handlers(host, endpoint, endpoint_definition, app_ctx)
                self.app.add_handlers(*handler)

        for url_patttern, static_file_handler_settings in self.static_file_handler_definitions:
            self.app.add_handlers(".*$", [(url_patttern, StaticFileHandler, static_file_handler_settings)])

        for periodic_function_definition in self.periodic_function_definitions:
            cb = periodic_function_definition.register(self.ioloop)
            cb.start()
            self.periodic_functions.append(cb)

        self.is_builded = True

    def run(self, port=8888, host="0.0.0.0"):
        if not self.is_builded:
            self.build_app(host=host)

        for on_start_func in self.on_start_funcs:
            self.ioloop.run_sync(functools.partial(on_start_func, self))

        self.app.listen(port)
        self.ioloop.start()

    def _create_dynamic_handlers(self, host, endpoint, endpoint_definition, app_ctx):
        return host, [
                (endpoint,
                 DynamicHandler,
                 dict(
                     endpoint_definition=endpoint_definition,
                     endpoint=endpoint,
                     user_loader_func=self.user_loader_func,
                     after_request_funcs=self.after_request_funcs,
                     error_handler_funcs=self.error_handlers,
                     before_request_funcs=self.before_request_funcs,
                     run_in_executor=self.run_in_executor,
                     teardown_request_funcs=self.teardown_request_funcs,
                     caches=self.caches,
                     app_ctx = app_ctx
                     )
                 )]

    def run_in_executor(self, func, *args):
        return self.ioloop.asyncio_loop.run_in_executor(self.executor, functools.partial(func, *args))

    def add_periodic_callback(self, func_time, func, *args):
        cb = PeriodicCallback(functools.partial(func, *args), func_time)
        cb.start()
        self.periodic_functions.append(cb)

    def run_periodic(self, interval=None, *args):
        def decorator(f):
            self.periodic_function_definitions.append(PeriodicCallbackDef(f,  interval=interval , *args))
            return f
        return decorator

    def on_start(self, f):
        self.on_start_funcs.insert(0,f)
        return f

    def error_handler(self, err_type=None):
        def decorator(f):
            self.error_handlers[err_type] = f
            return f

        return decorator

    def add_tornado_handler(self, host_pattern, host_handlers):
        self.app.add_handlers(host_pattern, host_handlers)

class ApplicationContext(_HandlerBoundObject):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class PeriodicCallbackDef(object):

    def __init__(self, f, interval=None, *args):
        self.f = f
        self._interval = interval
        self._args = args


    def register(self, ioloop):
        f = self._get_binded_function()
        return PeriodicCallback(f, self._interval)

    def _get_binded_function(self):
        if self._args and len(self._args) > 0:
            return functools.partial(self.f, *self._args)
        return self.f


