// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

abstract contract ERC20 {
    mapping(address => uint256) private _balances;
    uint256 private _totalSupply;

    function totalSupply() public view virtual returns (uint256) {
        return _totalSupply;
    }

    function balanceOf(address account) public view virtual returns (uint256) {
        return _balances[account];
    }

    function transfer(address to, uint256 amount) public virtual returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero account");
        uint256 fromBalance = _balances[msg.sender];
        require(fromBalance >= amount, "ERC20: transfer amount exceeds balance");
        _balances[msg.sender] = fromBalance - amount;
        _balances[to] += amount;
        return true;
    }
}
