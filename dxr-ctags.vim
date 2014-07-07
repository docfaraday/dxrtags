set tags=dxr-ctags,./dxr-ctags;

" Performs the query we want using dxr-ctags.py, which updates dxr-ctags with
" only the matches we're interested in. Once this is done, we turn it over to
" vim's ctags support.
function PerformQuery(query_type, token)
    let args = '-q '.a:query_type.' -f '.expand('%').' -l '.line('.').' -t '.a:token
    let command = 'dxr-ctags.py '.args
    call system(command)
endfunction

function PerformQueryContextFree(query_type, token)
    let args = '-q '.a:query_type.' -t '.a:token
    let command = 'dxr-ctags.py '.args
    call system(command)
endfunction

function Dxtjump(query_type, token)
    call PerformQuery(a:query_type, a:token)
    exe "tjump ".a:token
endfunction

function Dxtjump_cf(query_type, token)
    call PerformQueryContextFree(a:query_type, a:token)
    exe "tjump ".a:token
endfunction

function Dxstjump(query_type, token)
    call PerformQuery(a:query_type, a:token)
    exe "stjump ".a:token
endfunction

function Dxstjump_cf(query_type, token)
    call PerformQueryContextFree(a:query_type, a:token)
    exe "stjump ".a:token
endfunction

function Dxvtjump(query_type, token)
    call PerformQuery(a:query_type, a:token)
    exe "vert stjump ".a:token
endfunction

function Dxvtjump_cf(query_type, token)
    call PerformQueryContextFree(a:query_type, a:token)
    exe "vert stjump ".a:token
endfunction

"
" The following key mappings are derived from 'gtags-cscope.vim', which were
" in turn derived from 'cscope_maps.vim'.
"
" normal command
:nmap <C-\>s :call Dxtjump('refs', expand("<cword>"))<CR>
:nmap <C-\>g :call Dxtjump('defs', expand("<cword>"))<CR>
:nmap <C-\>d :call Dxtjump('decls',expand("<cword>"))<CR>
:nmap <C-\>f :call Dxtjump('files',expand("<cfile>"))<CR>
" try harder
:nmap <C-\>S :call Dxtjump_cf('refs', expand("<cword>"))<CR>
:nmap <C-\>G :call Dxtjump_cf('defs', expand("<cword>"))<CR>
:nmap <C-\>D :call Dxtjump_cf('decls',expand("<cword>"))<CR>
:nmap <C-\>F :call Dxtjump_cf('files',expand("<cfile>"))<CR>
"" Using 'CTRL-]', the result is displayed in new horizontal window.
:nmap <C-]>s :call Dxstjump('refs', expand("<cword>"))<CR>
:nmap <C-]>g :call Dxstjump('defs', expand("<cword>"))<CR>
:nmap <C-]>d :call Dxstjump('decls',expand("<cword>"))<CR>
:nmap <C-]>f :call Dxstjump('files',expand("<cfile>"))<CR>
" try harder
:nmap <C-]>S :call Dxstjump_cf('refs', expand("<cword>"))<CR>
:nmap <C-]>G :call Dxstjump_cf('defs', expand("<cword>"))<CR>
:nmap <C-]>D :call Dxstjump_cf('decls',expand("<cword>"))<CR>
:nmap <C-]>F :call Dxstjump_cf('files',expand("<cfile>"))<CR>
"" Hitting CTRL-] *twice*, the result is displayed in new vertical window.
:nmap <C-]><C-]>s :call Dxvtjump('refs', expand("<cword>"))<CR>
:nmap <C-]><C-]>g :call Dxvtjump('defs', expand("<cword>"))<CR>
:nmap <C-]><C-]>d :call Dxvtjump('decls',expand("<cword>"))<CR>
:nmap <C-]><C-]>f :call Dxvtjump('files',expand("<cfile>"))<CR>
" try harder
:nmap <C-]><C-]>S :call Dxvtjump_cf('refs', expand("<cword>"))<CR>
:nmap <C-]><C-]>G :call Dxvtjump_cf('defs', expand("<cword>"))<CR>
:nmap <C-]><C-]>D :call Dxvtjump_cf('decls',expand("<cword>"))<CR>
:nmap <C-]><C-]>F :call Dxvtjump_cf('files',expand("<cfile>"))<CR>

