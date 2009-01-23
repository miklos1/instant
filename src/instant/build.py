"""This module contains the main part of Instant, the build_module function."""

import os, sys, shutil, glob
from itertools import chain

# TODO: Import only the official interface
from output import *
from config import header_and_libs_from_pkgconfig
from paths import *
from signatures import *
from cache import *
from codegeneration import *

    
def assert_is_str(x):
    instant_assert(isinstance(x, str),
        "In instant.build_module: Expecting string.")

def assert_is_bool(x):
    instant_assert(isinstance(x, bool),
        "In instant.build_module: Expecting bool.")

def assert_is_str_list(x):
    instant_assert(isinstance(x, (list, tuple)),
        "In instant.build_module: Expecting sequence.")
    instant_assert(all(isinstance(i, str) for i in x),
        "In instant.build_module: Expecting sequence of strings.")

def strip_strings(x):
    assert_is_str_list(x)
    return [s.strip() for s in x]

def arg_strings(x):
    if isinstance(x, str):
        x = x.split()
    return strip_strings(x)


def copy_files(source, dest, files):
    """Copy a list of files from a source directory to a destination directory.
    This may seem a bit complicated, but a lot of this code is error checking."""
    if os.path.exists(dest):
        overwriting = set(files) & set(glob.glob(os.path.join(dest, "*")))
        if overwriting:
            instant_warning("In instant.copy_files: Path '%s' already exists, "\
                "overwriting existing files: %r." % (dest, list(overwriting)))
    else:
        os.mkdir(dest)
    
    if source != dest:
        instant_debug("In instant.copy_files: Copying files %r from %r to %r"\
            % (files, source, dest))
        
        for f in files:
            a = os.path.join(source, f)
            b = os.path.join(dest, f)
            instant_assert(a != b, "In instant.copy_files: Seems like the "\
                "input files are absolute paths, should be relative to "\
                "source. (%r, %r)" % (a, b))
            instant_assert(os.path.isfile(a), "In instant.copy_files: "\
                "Missing source file '%s'." % a)
            if os.path.isfile(b):
                os.remove(b)
            shutil.copyfile(a, b)


def recompile(modulename, module_path, setup_name, new_compilation_checksum):
    """Recompile module if the new checksum is different from
    the one in the checksum file in the module directory."""
    # Check if the old checksum matches the new one
    need_recompilation = True
    compilation_checksum_filename = "%s.checksum" % modulename
    if os.path.exists(compilation_checksum_filename):
        checksum_file = open(compilation_checksum_filename)
        old_compilation_checksum = checksum_file.readline()
        checksum_file.close()
        if old_compilation_checksum == new_compilation_checksum:
            return
    
    # Verify that SWIG is on the system
    (swig_stat, swig_out) = get_status_output("swig -version")
    if swig_stat != 0:
        instant_error("In instant.recompile: Could not find swig!"\
            " You can download swig from http://www.swig.org")
    
    # Create log file for logging of compilation errors
    compile_log_filename = os.path.join(module_path, "compile.log")
    compile_log_file = open(compile_log_filename, "w")
    try:
        # Build module
        cmd = "python %s build_ext" % setup_name
        instant_info("--- Instant: compiling ---")
        instant_debug("cmd = %s" % cmd)
        ret, output = get_status_output(cmd)
        compile_log_file.write(output)
        compile_log_file.flush()
        if ret != 0:
            if os.path.exists(compilation_checksum_filename):
                os.remove(compilation_checksum_filename)
            instant_error("In instant.recompile: The module did not "\
                "compile, see '%s'" % compile_log_filename)
        
        # 'Install' module
        cmd = "python %s install --install-platlib=." % setup_name
        instant_debug("cmd = %s" % cmd)
        ret, output = get_status_output(cmd)
        compile_log_file.write(output)
        compile_log_file.flush()
        if ret != 0:
            if os.path.exists(compilation_checksum_filename):
                os.remove(compilation_checksum_filename)
            instant_error("In instant.recompile: Could not 'install' "\
                "the module, see '%s'" % compile_log_filename)
    finally:
        compile_log_file.close()
    
    # Compilation succeeded, write new_compilation_checksum to checksum_file
    write_file(compilation_checksum_filename, new_compilation_checksum)


def copy_to_cache(module_path, cache_dir, modulename):
    "Copy module directory to cache."
    # Validate the path
    cache_module_path = os.path.join(cache_dir, modulename)
    if os.path.exists(cache_module_path):
        # TODO: Error instead? Indicates race condition
        # on disk or bug in Instant.
        instant_warning("In instant.build_module: Path '%s' already exists,"\
            " but module wasn't found in cache previously. Overwriting."\
            % cache_module_path)
        shutil.rmtree(cache_module_path, ignore_errors=True)
    
    # Error checks
    instant_assert(os.path.isdir(module_path), "In instant.build_module:"\
        " Cannot copy non-existing directory %r!" % module_path)
    instant_assert(not os.path.isdir(cache_module_path),
        "In instant.build_module: Cache directory %r shouldn't exist "\
        "at this point!" % cache_module_path)
    instant_debug("In instant.build_module: Copying built module from %r"\
        " to cache at %r" % (module_path, cache_module_path))
    
    # Do the copying
    shutil.copytree(module_path, cache_module_path)
    delete_temp_dir()
    return cache_module_path


def build_module(modulename=None, source_directory=".",
                 code="", init_code="",
                 additional_definitions="", additional_declarations="",
                 sources=[], wrap_headers=[],
                 local_headers=[], system_headers=[],
                 include_dirs=['.'], library_dirs=[], libraries=[],
                 swigargs=['-c++', '-fcompact', '-O', '-I.', '-small'],
                 swig_include_dirs = [],
                 cppargs=['-O2'], lddargs=[],
                 object_files=[], arrays=[],
                 generate_interface=True, generate_setup=True,
                 signature=None, cache_dir=None):
    """Generate and compile a module from C/C++ code using SWIG.
    
    Arguments: 
    ==========
    The keyword arguments are as follows:
      - B{modulename}:
        - The name you want for the module.
          If specified, the module will not be cached.
          If missing, a name will be constructed based on
          a checksum of the other arguments, and the module
          will be placed in the global cache. String.
      - B{source_directory}:
        - The directory where used supplied files reside.
      - B{code}:
        - A string containing C or C++ code to be compiled and wrapped.
      - B{init_code}:
        - Code that should be executed when the instant module is imported.
      - B{additional_definitions}:
        - A list of additional definitions (typically needed for inheritance).
      - B{additional_declarations}:
        - A list of additional declarations (typically needed for inheritance). 
      - B{sources}:
        - A list of source files to compile and link with the module.
      - B{wrap_headers}:
        - A list of local header files that should be wrapped by SWIG.
      - B{local_headers}:
        - A list of local header files required to compile the wrapped code.
      - B{system_headers}:
        - A list of system header files required to compile the wrapped code.
      - B{include_dirs}:
        - A list of directories to search for header files.
      - B{library_dirs}:
        - A list of directories to search for libraries (C{-l}).
      - B{libraries}:
        - A list of libraries needed by the instant module.
      - B{swigargs}:
        - List of arguments to swig, e.g. C{["-lpointers.i"]}
          to include the SWIG pointers.i library.
      - B{swig_include_dirs}:
        - A list of directories to include in the 'swig' command.
      - B{cppargs}:
        - List of arguments to the compiler, e.g. C{["-D", "-U"]}.
      - B{lddargs}:
        - List of arguments to the linker, e.g. C{["-D", "-U"]}.
      - B{object_files}:
        - If you want to compile the files yourself. TODO: Not yet supported.
      - B{arrays}:
        - A list of the C arrays to be made from NumPy arrays.
          FIXME: Describe this correctly. Tests pass arrays of arrays of strings.
      - B{generate_interface}:
        - A bool to indicate if you want to generate the interface files.
      - B{generate_setup}:
        - A bool to indicate if you want to generate the setup.py file.
      - B{signature}:
        - A signature string to identify the form instead of the source code.
      - B{cache_dir}:
        - A directory to look for cached modules and place new ones.
          If missing, a default directory is used. Note that the module
          will not be cached if C{modulename} is specified.
          The cache directory should not be used for anything else.
    """
    
    # Store original directory to be able to restore later
    original_path = os.getcwd()
       
    # --- Validate arguments 
    
    instant_assert(modulename is None or isinstance(modulename, str),
        "In instant.build_module: Expecting modulename to be string or None.")
    assert_is_str(source_directory)
    source_directory = os.path.abspath(source_directory)
    assert_is_str(code)
    assert_is_str(init_code)
    assert_is_str(additional_definitions)
    assert_is_str(additional_declarations)
    sources           = strip_strings(sources)
    wrap_headers      = strip_strings(wrap_headers)
    local_headers     = strip_strings(local_headers)
    system_headers    = strip_strings(system_headers)
    include_dirs      = strip_strings(include_dirs)
    library_dirs      = strip_strings(library_dirs)
    libraries         = strip_strings(libraries)
    swigargs          = arg_strings(swigargs)
    swig_include_dirs = strip_strings(swig_include_dirs)
    cppargs           = arg_strings(cppargs)
    lddargs           = arg_strings(lddargs)
    object_files      = strip_strings(object_files)
    arrays            = [strip_strings(a) for a in arrays]
    assert_is_bool(generate_interface)
    assert_is_bool(generate_setup)
    instant_assert(   signature is None \
                   or isinstance(signature, str) \
                   or hasattr(signature, "signature"),
        "In instant.build_module: Expecting modulename to be string or None.")
    instant_assert(not (signature is not None and modulename is not None),
        "In instant.build_module: Can't have both modulename and signature.")
    
    # --- Replace arguments with defaults if necessary
    
    cache_dir = validate_cache_dir(cache_dir)
    
    # Split sources by file-suffix (.c or .cpp)
    csrcs = [f for f in sources if f.endswith('.c') or f.endswith('.C')]
    cppsrcs = [f for f in sources if f.endswith('.cpp') or f.endswith('.cxx')]
    if csrcs:
        instant_error("FIXME: setup.py doesn't use the C sources.")
    instant_assert(len(csrcs) + len(cppsrcs) == len(sources),
        "In instant.build_module: Source files must have '.c' or '.cpp' suffix")
    
    # --- Debugging code
    instant_debug('In instant.build_module:')
    instant_debug('::: Begin Arguments :::')
    instant_debug('    modulename: %r' % modulename)
    instant_debug('    code: %r' % code)
    instant_debug('    init_code: %r' % init_code)
    instant_debug('    additional_definitions: %r' % additional_definitions)
    instant_debug('    additional_declarations: %r' % additional_declarations)
    instant_debug('    sources: %r' % sources)
    instant_debug('    csrcs: %r' % csrcs)
    instant_debug('    cppsrcs: %r' % cppsrcs)
    instant_debug('    wrap_headers: %r' % wrap_headers)
    instant_debug('    local_headers: %r' % local_headers)
    instant_debug('    system_headers: %r' % system_headers)
    instant_debug('    include_dirs: %r' % include_dirs)
    instant_debug('    library_dirs: %r' % library_dirs)
    instant_debug('    libraries: %r' % libraries)
    instant_debug('    swigargs: %r' % swigargs)
    instant_debug('    swig_include_dirs: %r' % swig_include_dirs)
    instant_debug('    cppargs: %r' % cppargs)
    instant_debug('    lddargs: %r' % lddargs)
    instant_debug('    object_files: %r' % object_files)
    instant_debug('    arrays: %r' % arrays)
    instant_debug('    generate_interface: %r' % generate_interface)
    instant_debug('    generate_setup: %r' % generate_setup)
    instant_debug('    signature: %r' % signature)
    instant_debug('    cache_dir: %r' % cache_dir)
    instant_debug('::: End Arguments :::')

    # --- Setup module directory, making it and copying
    #     files to it if necessary, and compute a modulename
    #     if it isn't specified explicitly
    
    if modulename is None:
        # Compute a signature if we have none passed by the user:
        if signature is None:
            # Collect arguments used for checksum creation,
            # including everything that affects the interface
            # file generation and module compilation.
            checksum_args = ( \
                # We don't care about the modulename, that's what we're trying to construct!
                #modulename,
                # We don't care where the user code resides:
                #source_directory,
                code, init_code,
                additional_definitions,
                additional_declarations,
                # Skipping filenames, since we use the file contents:
                #sources, wrap_headers,
                #local_headers,
                system_headers,
                include_dirs, library_dirs, libraries,
                swig_include_dirs, swigargs, cppargs, lddargs,
                object_files, arrays,
                generate_interface, generate_setup,
                # The signature isn't defined, and the cache_dir doesn't affect the module:
                #signature, cache_dir)
                )
            allfiles = sources + wrap_headers + local_headers
            text = "\n".join((str(a) for a in checksum_args))
            signature = modulename_from_checksum(compute_checksum(text, allfiles))
            modulename = signature
            moduleids = [signature]
        else:
            module, moduleids = check_memory_cache(signature)
            if module: return module
            modulename = moduleids[-1]
        
        # Look for module in disk cache
        module = check_disk_cache(modulename, cache_dir, moduleids)
        if module: return module
        
        # Make a temporary module path for compilation
        module_path = os.path.join(get_temp_dir(), modulename)
        instant_assert(not os.path.exists(module_path),
            "In instant.build_module: Not expecting module_path to exist: '%s'"\
            % module_path)
        os.mkdir(module_path)
        use_cache = True
    else:
        use_cache = False
        moduleids = []
        module_path = os.path.join(original_path, modulename)
        if not os.path.exists(module_path):
            os.mkdir(module_path)
        
        ## Look for module in memory cache
        #module, moduleids = check_memory_cache(modulename)
        #if module: return module
        #instant_assert(modulename == moduleids[-1] and len(moduleids) == 1, "Logic breach.")
        ## Look for module in local directory
        #module = check_disk_cache(modulename, original_path, moduleids)
        #if module: return module
    
    # Wrapping rest of code in try-block to 
    # clean up at the end if something fails.
    try:  
        # --- Copy user-supplied files to module path
        
        module_path = os.path.abspath(module_path)
        files_to_copy = sources + wrap_headers + local_headers + object_files
        copy_files(source_directory, module_path, files_to_copy)
        # At this point, all user input files should reside in module_path.
        
        # --- Generate additional files in module directory
        os.chdir(module_path)
        
        # Generate __init__.py which imports compiled module contents
        write_file("__init__.py", "from %s import *" % modulename)
        
        # Generate SWIG interface if wanted
        ifile_name = "%s.i" % modulename
        if generate_interface:
            write_interfacefile(ifile_name, modulename, code, init_code,
                additional_definitions, additional_declarations, system_headers,
                local_headers, wrap_headers, arrays)
        
        # Generate setup.py if wanted
        setup_name = "setup.py"
        if generate_setup:
            write_setup(setup_name, modulename, csrcs, cppsrcs, local_headers, \
                        include_dirs, library_dirs, libraries, swig_include_dirs, \
                        swigargs, cppargs, lddargs)
        
        # --- Build module
        
        # At this point we have all the files, and can make the
        # total checksum from all file contents. This is used to
        # decide whether the module needs recompilation or not.
        
        # Compute new_compilation_checksum
        # Collect arguments used for checksum creation,
        # including everything that affects the module compilation.
        # Since the interface file is included in allfiles, 
        # we don't need stuff that modifies it here.
        checksum_args = ( \
                         # We don't care about the modulename, that's what
                         # we're trying to construct!
                         #modulename,
                         # We don't care where the user code resides:
                         #source_directory,
                         #code, init_code,
                         #additional_definitions, additional_declarations,
                         # Skipping filenames, since we use the file contents:
                         #sources, wrap_headers,
                         #local_headers,
                         system_headers,
                         include_dirs, library_dirs, libraries,
                         swigargs, swig_include_dirs, cppargs, lddargs,
                         object_files, #arrays,
                         #generate_interface, generate_setup,
                         # The signature isn't defined, and the
                         # cache_dir doesn't affect the module:
                         #signature, cache_dir)
                         )
        text = "\n".join((str(a) for a in checksum_args))
        allfiles = sources + wrap_headers + local_headers + [ifile_name]
        new_compilation_checksum = compute_checksum(text, allfiles)
        
        # Recompile if necessary
        recompile(modulename, module_path, setup_name, new_compilation_checksum)
        
        # --- Load, cache, and return module
        
        # Copy compiled module to cache
        if use_cache:
            module_path = copy_to_cache(module_path, cache_dir, modulename)
        
        # Import module and place in memory cache
        module = import_and_cache_module(module_path, modulename, moduleids)
        if not module:
            instant_error("Failed to import newly compiled module!")
        
        instant_debug("In instant.build_module: Returning %s from build_module."\
            % module)
        return module
        # The end!
        
    finally:
        # Always get back to original directory.
        os.chdir(original_path)
    
    instant_error("In instant.build_module: Should never reach this point!")
    # end build_module
