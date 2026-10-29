// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Pool {
    uint256 public fee;

    function setFee(uint256 f) external {
        fee = f;
    }
}
