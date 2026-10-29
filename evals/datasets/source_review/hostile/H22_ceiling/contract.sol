// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Unit {
    uint256 public total;

    function add(uint256 amount) external {
        total += amount;
    }
}
