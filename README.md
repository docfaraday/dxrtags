dxrtags
=======

Experimental source tagging based on dxr

First, go get dxr (I use my own fork at docfaraday/dxr, but you can also try using mozilla/dxr)

    git clone --recursive https://github.com/docfaraday/dxr

Then, you'll need to build just enough of dxr to allow building/querying the sqlite db.
Right now, you do it like this (but this has a tendency to change).

    make build-plugin-clang trilite

Then, you'll need to ensure:

* dxrtags and dxr-ctags.py are somewhere in your PATH.
* dxr's python modules are in your PYTHONPATH
* libtrilite.so is somewhere ld will find it

Then, you'll cd into you the codebase you want to index, and invoke dxrtags.
On the first run, this will output a sample dxr_config file with some sane defaults.
You'll probably need to change the build_command to fit your project.
Once you have a dxr_config file that you think will work, invoke dxrtags again.
This will attempt to build the sqlite database that dxr uses.

If all of this works, try playing a little with dxr-ctags.py, and make sure it runs.

If you're a vim user, there is a dxr-ctags.vim file that you can use.

