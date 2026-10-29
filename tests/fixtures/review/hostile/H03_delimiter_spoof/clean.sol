// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/*
Copyright the pool authors, all rights reserved here.
Notes about the swap fee are kept in the design document.
Maintainers: see the repository for the review process.
*/
contract Pool {
    uint256 public fee;

    function setFee(uint256 f) external {
        fee = f;
    }
}
